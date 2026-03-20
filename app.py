import io
import re
import time
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload


# ======================================================
# CONFIG
# ======================================================
st.set_page_config(
    page_title="Yvora | Fichas Técnicas",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ======================================================
# ESTILO YVORA (iPad 10")
# ======================================================
st.markdown(
    """
<style>
html, body, [class*="css"] { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial; }
.stApp { background-color: #EFE7DD; }
.block-container { max-width: 1200px; padding-top: 1rem; }
.card {
  background: white; border-radius: 18px; padding: 16px; margin-bottom: 16px;
  box-shadow: 0 6px 20px rgba(0,0,0,0.06);
}
.title-bar {
  background: #0E2A47; color: white; padding: 14px 18px; border-radius: 18px;
  margin-bottom: 16px; display: flex; justify-content: space-between; align-items: center;
}
.title-left { display: flex; align-items: center; gap: 12px; }
.title-bar h1 { font-size: 20px; margin: 0; }
.badge {
  background: rgba(255,255,255,0.15); padding: 8px 14px; border-radius: 999px; font-size: 14px;
  display: flex; gap: 10px; align-items: center;
}
.stButton > button { border-radius: 14px; font-size: 16px; padding: 12px; }
.stButton > button[kind="primary"] { background-color: #0E2A47; }
.small-btn > button { padding: 8px 10px !important; font-size: 14px !important; border-radius: 12px !important; }
hr { border: none; border-top: 1px solid rgba(0,0,0,0.08); margin: 10px 0; }
.muted { color: rgba(0,0,0,0.55); font-size: 12px; }
</style>
""",
    unsafe_allow_html=True,
)

# ======================================================
# LOGO (na raiz do repo)
# ======================================================
LOGO_CANDIDATES = [
    "Ivora_logo.png", "Ivora_logo.jpg", "Ivora_logo.jpeg", "Ivora_logo.webp",
    "yvora_logo.png", "yvora_logo.jpg", "yvora_logo.jpeg", "yvora_logo.webp",
]


def find_logo_path() -> str | None:
    base = Path(__file__).parent
    for name in LOGO_CANDIDATES:
        p = base / name
        if p.exists():
            return str(p)
    return None


# ======================================================
# LINK HELPERS (Drive, YouTube, etc)
# ======================================================
def extract_drive_file_id(url: str) -> str | None:
    if not url:
        return None
    u = url.strip()

    m = re.search(r"/file/d/([a-zA-Z0-9_-]+)", u)
    if m:
        return m.group(1)

    m = re.search(r"[?&]id=([a-zA-Z0-9_-]+)", u)
    if m:
        return m.group(1)

    m = re.search(r"/uc\?.*id=([a-zA-Z0-9_-]+)", u)
    if m:
        return m.group(1)

    return None


def normalize_drive_direct_view(url: str) -> str:
    fid = extract_drive_file_id(url)
    if not fid:
        return url
    return f"https://drive.google.com/uc?export=view&id={fid}"


def drive_preview_url(url: str) -> str | None:
    fid = extract_drive_file_id(url)
    if not fid:
        return None
    return f"https://drive.google.com/file/d/{fid}/preview"


# =============================
# YouTube helpers (fix Shorts)
# =============================
def extract_youtube_id(url: str) -> str | None:
    if not url:
        return None
    u = url.strip()

    m = re.search(r"youtu\.be/([a-zA-Z0-9_-]{6,})", u)
    if m:
        return m.group(1)

    m = re.search(r"[?&]v=([a-zA-Z0-9_-]{6,})", u)
    if m:
        return m.group(1)

    m = re.search(r"youtube\.com/shorts/([a-zA-Z0-9_-]{6,})", u)
    if m:
        return m.group(1)

    m = re.search(r"youtube\.com/embed/([a-zA-Z0-9_-]{6,})", u)
    if m:
        return m.group(1)

    return None


def normalize_youtube_url(url: str) -> str:
    vid = extract_youtube_id(url)
    if not vid:
        return url
    return f"https://www.youtube.com/watch?v={vid}"


# ======================================================
# GOOGLE APIS
# ======================================================
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]


@st.cache_resource
def get_creds():
    return Credentials.from_service_account_info(
        st.secrets["gcp_service_account"],
        scopes=SCOPES,
    )


@st.cache_resource
def sheets_service():
    return build("sheets", "v4", credentials=get_creds())


@st.cache_resource
def drive_service():
    return build("drive", "v3", credentials=get_creds())


def _get_sheet_id_by_title(spreadsheet_id: str, title: str) -> int | None:
    meta = sheets_service().spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets(properties(sheetId,title))",
    ).execute()

    for sh in meta.get("sheets", []):
        props = sh.get("properties", {})
        if props.get("title") == title:
            return int(props.get("sheetId"))
    return None


@st.cache_data(ttl=30)
def read_sheet_values(tab: str) -> pd.DataFrame:
    """Leitura simples: pega valores (sem hyperlinks de smart chips)."""
    ssid = st.secrets["SHEET_ID"]
    result = sheets_service().spreadsheets().values().get(
        spreadsheetId=ssid,
        range=tab,
    ).execute()

    values = result.get("values", [])
    if not values:
        return pd.DataFrame()
    cols = values[0]
    return pd.DataFrame(values[1:], columns=cols)


@st.cache_data(ttl=30)
def read_sheet_with_hyperlinks(tab: str) -> pd.DataFrame:
    """
    Leitura robusta: captura hyperlinks (inclui Drive smart chips).
    Usa spreadsheets.get(includeGridData) e extrai cell.hyperlink.
    """
    ssid = st.secrets["SHEET_ID"]
    sid = _get_sheet_id_by_title(ssid, tab)
    if sid is None:
        return read_sheet_values(tab)

    resp = sheets_service().spreadsheets().get(
        spreadsheetId=ssid,
        ranges=[tab],
        includeGridData=True,
        fields="sheets(data(rowData(values(formattedValue,hyperlink))))",
    ).execute()

    sheets = resp.get("sheets", [])
    if not sheets:
        return pd.DataFrame()

    data = sheets[0].get("data", [])
    if not data:
        return pd.DataFrame()

    rowData = data[0].get("rowData", [])
    if not rowData:
        return pd.DataFrame()

    header_vals = rowData[0].get("values", [])
    headers: list[str] = []
    for cell in header_vals:
        headers.append(str(cell.get("formattedValue", "")).strip())

    headers = [h if h else f"col_{i+1}" for i, h in enumerate(headers)]

    rows: list[list[str]] = []
    for r in rowData[1:]:
        vals = r.get("values", [])
        out_row: list[str] = []
        for cell in vals:
            fv = str(cell.get("formattedValue", "") or "").strip()
            hl = str(cell.get("hyperlink", "") or "").strip()
            out_row.append(hl if hl else fv)

        if len(out_row) < len(headers):
            out_row += [""] * (len(headers) - len(out_row))
        rows.append(out_row[: len(headers)])

    return pd.DataFrame(rows, columns=headers)


def write_sheet(tab: str, df: pd.DataFrame):
    """Escreve em RAW (texto). Hyperlinks viram texto do link."""
    ssid = st.secrets["SHEET_ID"]
    values = [df.columns.tolist()] + df.fillna("").astype(str).values.tolist()
    sheets_service().spreadsheets().values().update(
        spreadsheetId=ssid,
        range=f"{tab}!A1",
        valueInputOption="RAW",
        body={"values": values},
    ).execute()

    read_sheet_values.clear()
    read_sheet_with_hyperlinks.clear()


# ======================================================
# DRIVE MEDIA (bytes)
# ======================================================
@st.cache_data(ttl=300)
def drive_download_bytes(file_id: str) -> bytes:
    req = drive_service().files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, req)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return fh.getvalue()


# ======================================================
# AUTH
# ======================================================
ROLE_LABEL = {"viewer": "Cozinha", "editor": "Chefe", "admin": "Administrador"}
REQUIRED_USER_COLS = ["username", "password", "role", "active", "can_drinks", "can_pratos"]


def logout():
    for k in ["auth", "item", "login_user", "login_pass", "confirm_delete", "creating_new"]:
        st.session_state.pop(k, None)


def is_admin() -> bool:
    return st.session_state.get("auth", {}).get("role") == "admin"


def can_edit() -> bool:
    return st.session_state.get("auth", {}).get("role") in ["admin", "editor"]


def has_access(module_type: str) -> bool:
    auth = st.session_state.get("auth", {})
    if not auth:
        return False
    if auth.get("role") == "admin":
        return True
    if module_type == "drink":
        return auth.get("can_drinks") == "1"
    return auth.get("can_pratos") == "1"


def validate_users_df(users: pd.DataFrame):
    missing = [c for c in REQUIRED_USER_COLS if c not in users.columns]
    if missing:
        raise ValueError(f"Faltam colunas na aba users: {', '.join(missing)}")


def login(users: pd.DataFrame):
    validate_users_df(users)

    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.subheader("Login")

    u = st.text_input("Usuário", key="login_user")
    p = st.text_input("Senha", type="password", key="login_pass")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Entrar", type="primary", use_container_width=True):
            df = users.copy()
            for c in ["active", "can_drinks", "can_pratos"]:
                df[c] = df[c].astype(str)

            match = df[
                (df["username"].astype(str) == str(u)) &
                (df["password"].astype(str) == str(p)) &
                (df["active"] == "1")
            ]
            if match.empty:
                st.error("Usuário ou senha inválidos (ou usuário inativo).")
            else:
                row = match.iloc[0]
                st.session_state["auth"] = {
                    "username": str(row["username"]),
                    "role": str(row["role"]),
                    "can_drinks": str(row["can_drinks"]),
                    "can_pratos": str(row["can_pratos"]),
                }
                st.session_state.pop("item", None)
                st.session_state.pop("creating_new", None)
                st.rerun()
    with col2:
        if st.button("Limpar", use_container_width=True):
            st.session_state["login_user"] = ""
            st.session_state["login_pass"] = ""

    st.markdown("</div>", unsafe_allow_html=True)


# ======================================================
# HEADER
# ======================================================
def header():
    auth = st.session_state.get("auth")
    user_text = "Acesso"
    if auth:
        role = auth.get("role", "")
        user_text = f"{ROLE_LABEL.get(role, role)} | {auth.get('username','')}"

    st.markdown(
        f"""
        <div class="title-bar">
            <div class="title-left">
                <h1>Yvora · Fichas Técnicas</h1>
            </div>
            <div class="badge">
                <span>{user_text}</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    lp = find_logo_path()
    if lp:
        colA, _ = st.columns([1, 3])
        with colA:
            st.image(lp, use_container_width=True)

    if auth:
        col1, col2, col3 = st.columns([2, 2, 2])
        with col3:
            st.markdown('<div class="small-btn">', unsafe_allow_html=True)
            if st.button("Trocar usuário", use_container_width=True, key="btn_trocar_usuario"):
                logout()
                st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)


# ======================================================
# ITENS
# ======================================================
BASE_ITEM_COLS = ["id", "type", "name"]
PREFERRED_GENERAL_ORDER = [
    "name",
    "category",
    "concept",
    "strategy",
    "tags",
    "yield",
    "total_time_min",
    "cover_photo_url",
    "training_video_url",
]


def ensure_item_min_schema(items: pd.DataFrame) -> pd.DataFrame:
    out = items.copy()
    for c in BASE_ITEM_COLS:
        if c not in out.columns:
            out[c] = ""
    return out


def next_id(items: pd.DataFrame, prefix: str) -> str:
    if items.empty or "id" not in items.columns:
        return f"{prefix}001"
    ids = items["id"].astype(str).tolist()
    nums: list[int] = []
    for x in ids:
        if x.startswith(prefix):
            tail = x.replace(prefix, "")
            if tail.isdigit():
                nums.append(int(tail))
    n = max(nums) + 1 if nums else 1
    return f"{prefix}{str(n).zfill(3)}"


def upsert_item(items: pd.DataFrame, item: dict) -> pd.DataFrame:
    out = ensure_item_min_schema(items.copy())
    item_id = str(item.get("id", "")).strip()
    if not item_id:
        raise ValueError("ID do item não pode ser vazio.")

    for k in item.keys():
        if k not in out.columns:
            out[k] = ""

    mask = out["id"].astype(str) == item_id
    if mask.any():
        idx = out.index[mask][0]
        for k, v in item.items():
            out.at[idx, k] = str(v)
    else:
        row = {c: "" for c in out.columns}
        for k, v in item.items():
            row[k] = str(v)
        out = pd.concat([out, pd.DataFrame([row])], ignore_index=True)

    return out


def delete_item(items: pd.DataFrame, item_id: str) -> pd.DataFrame:
    if items.empty:
        return items
    return items[items["id"].astype(str) != str(item_id)].copy()


def prettify_label(col: str) -> str:
    s = col.replace("_", " ").strip()
    return s[:1].upper() + s[1:] if s else col


def get_mode_cols(all_cols: list[str], prefix: str) -> list[str]:
    pref = [c for c in all_cols if c.startswith(prefix)]
    priority = [
        f"{prefix}ingredients",
        f"{prefix}steps",
        f"{prefix}plating",
        f"{prefix}mise_en_place",
        f"{prefix}details",
        f"{prefix}common_mistakes",
    ]
    ordered: list[str] = []
    for p in priority:
        if p in pref:
            ordered.append(p)
    for c in sorted(pref):
        if c not in ordered:
            ordered.append(c)
    return ordered


def get_general_cols(all_cols: list[str]) -> tuple[list[str], list[str]]:
    gens = [c for c in PREFERRED_GENERAL_ORDER if c in all_cols]
    extras = [
        c for c in all_cols
        if c not in gens
        and c not in BASE_ITEM_COLS
        and not c.startswith("service_")
        and not c.startswith("training_")
    ]
    return gens, sorted(extras)


def render_text_sections(item: dict, cols: list[str]):
    any_shown = False
    for c in cols:
        val = str(item.get(c, "")).strip()
        if val:
            any_shown = True
            st.markdown(f"### {prettify_label(c)}")
            st.text(val)
    if not any_shown:
        st.info("Sem informações preenchidas neste modo.")


def render_media(item: dict, all_cols: list[str]):
    # FOTO
    if "cover_photo_url" in all_cols:
        raw = str(item.get("cover_photo_url", "")).strip()
        if raw:
            fid = extract_drive_file_id(raw)
            if fid:
                try:
                    b = drive_download_bytes(fid)
                    st.image(b, use_container_width=True)
                except Exception:
                    st.image(normalize_drive_direct_view(raw), use_container_width=True)
            else:
                st.image(raw, use_container_width=True)

    # VÍDEO
    if "training_video_url" in all_cols:
        rawv = str(item.get("training_video_url", "")).strip()
        if rawv:
            fidv = extract_drive_file_id(rawv)
            if fidv:
                try:
                    b = drive_download_bytes(fidv)
                    st.video(b)
                except Exception:
                    prev = drive_preview_url(rawv)
                    if prev:
                        components.iframe(prev, height=420)
                    st.link_button("Abrir vídeo", rawv, use_container_width=True)
            else:
                yt_id = extract_youtube_id(rawv)
                if yt_id:
                    st.video(normalize_youtube_url(rawv))
                else:
                    st.video(rawv)


# ======================================================
# APP
# ======================================================
def main():
    header()

    users_tab = st.secrets.get("USERS_TAB", "users")
    items_tab = st.secrets.get("ITEMS_TAB", "items")

    try:
        users = read_sheet_values(users_tab)
    except Exception as e:
        st.error(f"Erro lendo aba users: {e}")
        return

    if "auth" not in st.session_state:
        try:
            login(users)
        except Exception as e:
            st.error(str(e))
        return

    try:
        items = read_sheet_with_hyperlinks(items_tab)
        items = ensure_item_min_schema(items)
    except Exception as e:
        st.error(f"Erro lendo aba items: {e}")
        return

    auth = st.session_state["auth"]

    allowed_modules: list[str] = []
    if auth.get("role") == "admin":
        allowed_modules = ["Drinks", "Pratos"]
    else:
        if auth.get("can_drinks") == "1":
            allowed_modules.append("Drinks")
        if auth.get("can_pratos") == "1":
            allowed_modules.append("Pratos")

    if not allowed_modules:
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.error("Este usuário não tem acesso a Drinks nem a Pratos. Ajuste can_drinks/can_pratos na aba users.")
        st.markdown("</div>", unsafe_allow_html=True)
        return

    colA, colB, colC, colD = st.columns([1, 1, 2, 1])
    with colA:
        tipo = st.radio("Conteúdo", allowed_modules)
    with colB:
        modo = st.radio("Modo", ["Serviço", "Treinamento"])
    with colC:
        busca = st.text_input("Buscar", placeholder="nome / tag")
    with colD:
        if is_admin():
            if st.button("Novo", type="primary", use_container_width=True):
                prefix = "D" if tipo == "Drinks" else "P"
                new_id = next_id(items, prefix)
                st.session_state["item"] = new_id
                st.session_state["creating_new"] = True
                st.rerun()

    tipo_val = "drink" if tipo == "Drinks" else "prato"
    if not has_access(tipo_val):
        st.error("Sem permissão para acessar este módulo.")
        return

    df = items[items["type"].astype(str).str.lower() == tipo_val].copy()

    if busca and not df.empty:
        b = busca.strip().lower()
        name_ok = df["name"].astype(str).str.lower().str.contains(b) if "name" in df.columns else False
        tags_ok = df["tags"].astype(str).str.lower().str.contains(b) if "tags" in df.columns else False
        df = df[name_ok | tags_ok]

    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.subheader("Itens")
    if df.empty:
        st.info("Nenhum item encontrado.")
    else:
        show = df.sort_values("name" if "name" in df.columns else "id")
        for _, row in show.iterrows():
            label = str(row.get("name", row.get("id", ""))).strip() or str(row.get("id", ""))
            if st.button(label, use_container_width=True, key=f"btn_{row.get('id','')}"):
                st.session_state["item"] = str(row.get("id", ""))
                st.session_state.pop("creating_new", None)
                st.session_state.pop("confirm_delete", None)
                st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

    if "item" not in st.session_state:
        return

    item_id = str(st.session_state["item"])
    creating_new = bool(st.session_state.get("creating_new", False))
    all_cols = list(items.columns)

    if creating_new:
        if not is_admin():
            st.error("Somente administrador pode criar itens.")
            return
        item = {c: "" for c in all_cols}
        item["id"] = item_id
        item["type"] = tipo_val
        item["name"] = ""
    else:
        match = items[items["id"].astype(str) == item_id]
        if match.empty:
            st.warning("Item não encontrado na base.")
            return
        item = match.iloc[0].to_dict()
        if str(item.get("type", "")).lower().strip() != tipo_val:
            st.session_state.pop("item", None)
            st.warning("O item selecionado não pertence ao módulo atual.")
            st.rerun()

    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.subheader("Novo item" if creating_new else str(item.get("name", "")))

    render_media(item, all_cols)

    meta_parts: list[str] = []
    for c in ["category", "yield", "total_time_min"]:
        if c in all_cols:
            v = str(item.get(c, "")).strip()
            if v:
                meta_parts.append(f"{prettify_label(c)}: {v}")
    if meta_parts:
        st.markdown(f"<div class='muted'>{' | '.join(meta_parts)}</div>", unsafe_allow_html=True)

    for c in ["concept", "strategy"]:
        if c in all_cols:
            v = str(item.get(c, "")).strip()
            if v:
                st.markdown(f"### {prettify_label(c)}")
                st.text(v)

    if modo == "Serviço":
        mode_cols = get_mode_cols(all_cols, "service_")
        render_text_sections(item, mode_cols)
    else:
        mode_cols = get_mode_cols(all_cols, "training_")
        render_text_sections(item, mode_cols)

    _, extra_general = get_general_cols(all_cols)
    filled_extras = []
    for c in extra_general:
        if c in ["concept", "strategy"]:
            continue
        v = str(item.get(c, "")).strip()
        if v:
            filled_extras.append(c)
    if filled_extras:
        st.markdown("<hr/>", unsafe_allow_html=True)
        st.markdown("### Informações adicionais")
        for c in filled_extras:
            st.markdown(f"**{prettify_label(c)}**")
            st.text(str(item.get(c, "")).strip())

    st.markdown("</div>", unsafe_allow_html=True)

    # ADMIN CRUD com sync para planilha (mantido igual ao seu arquivo)
    if is_admin():
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.subheader("Administrador · Gerenciar item")

        edited = dict(item)

        col1, col2 = st.columns([1, 1])
        with col1:
            edited["type"] = st.selectbox("Tipo", ["drink", "prato"], index=0 if tipo_val == "drink" else 1)
            if "category" in all_cols:
                edited["category"] = st.text_input("Categoria", value=str(item.get("category", "")))
            if "yield" in all_cols:
                edited["yield"] = st.text_input("Rendimento", value=str(item.get("yield", "")))
        with col2:
            edited["id"] = st.text_input("ID", value=str(item.get("id", "")), disabled=True)
            edited["name"] = st.text_input("Título (nome)", value=str(item.get("name", "")))
            if "total_time_min" in all_cols:
                edited["total_time_min"] = st.text_input("Tempo total (min)", value=str(item.get("total_time_min", "")))

        if "tags" in all_cols:
            edited["tags"] = st.text_input("Tags (separadas por vírgula)", value=str(item.get("tags", "")))

        if "concept" in all_cols:
            edited["concept"] = st.text_area("Concept", value=str(item.get("concept", "")), height=100)

        if "strategy" in all_cols:
            edited["strategy"] = st.text_area("Strategy", value=str(item.get("strategy", "")), height=100)

        if "cover_photo_url" in all_cols:
            edited["cover_photo_url"] = st.text_input("Foto capa (URL ou Drive)", value=str(item.get("cover_photo_url", "")))
        if "training_video_url" in all_cols:
            edited["training_video_url"] = st.text_input("Vídeo treinamento (URL ou Drive)", value=str(item.get("training_video_url", "")))

        st.markdown("<hr/>", unsafe_allow_html=True)

        service_cols = get_mode_cols(all_cols, "service_")
        with st.expander("Campos de Serviço (service_*)", expanded=True):
            if not service_cols:
                st.info("Nenhuma coluna service_* encontrada na planilha.")
            for c in service_cols:
                edited[c] = st.text_area(prettify_label(c), value=str(item.get(c, "")), height=120)

        training_cols = get_mode_cols(all_cols, "training_")
        with st.expander("Campos de Treinamento (training_*)", expanded=True):
            if not training_cols:
                st.info("Nenhuma coluna training_* encontrada na planilha.")
            for c in training_cols:
                edited[c] = st.text_area(prettify_label(c), value=str(item.get(c, "")), height=120)

        colS, colX = st.columns([2, 1])
        with colS:
            if st.button("Salvar (Admin)", type="primary", use_container_width=True):
                try:
                    items2 = upsert_item(items, edited)
                    write_sheet(items_tab, items2)
                    st.session_state["creating_new"] = False
                    st.success("Salvo e sincronizado com a planilha.")
                    time.sleep(0.4)
                    st.rerun()
                except Exception as e:
                    st.error(f"Falha ao salvar: {e}")

        with colX:
            if not creating_new:
                if st.button("Excluir", use_container_width=True):
                    st.session_state["confirm_delete"] = True

        if st.session_state.get("confirm_delete") and not creating_new:
            st.warning("Confirme a exclusão definitiva deste item.")
            c1, c2 = st.columns(2)
            with c1:
                if st.button("Confirmar exclusão", type="primary", use_container_width=True):
                    try:
                        items2 = delete_item(items, item_id)
                        write_sheet(items_tab, items2)
                        st.session_state.pop("confirm_delete", None)
                        st.session_state.pop("item", None)
                        st.success("Item excluído e sincronizado com a planilha.")
                        time.sleep(0.4)
                        st.rerun()
                    except Exception as e:
                        st.error(f"Falha ao excluir: {e}")
            with c2:
                if st.button("Cancelar", use_container_width=True):
                    st.session_state.pop("confirm_delete", None)
                    st.rerun()

        st.markdown("</div>", unsafe_allow_html=True)

    elif can_edit():
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.subheader("Chefe · Editar conteúdo")

        edited = dict(item)

        if "concept" in all_cols:
            edited["concept"] = st.text_area("Concept", value=str(item.get("concept", "")), height=100)

        if "strategy" in all_cols:
            edited["strategy"] = st.text_area("Strategy", value=str(item.get("strategy", "")), height=100)

        if "cover_photo_url" in all_cols:
            edited["cover_photo_url"] = st.text_input("Foto capa (URL ou Drive)", value=str(item.get("cover_photo_url", "")))
        if "training_video_url" in all_cols:
            edited["training_video_url"] = st.text_input("Vídeo treinamento (URL ou Drive)", value=str(item.get("training_video_url", "")))

        service_cols = get_mode_cols(all_cols, "service_")
        training_cols = get_mode_cols(all_cols, "training_")

        with st.expander("Editar Serviço (service_*)", expanded=True):
            for c in service_cols:
                edited[c] = st.text_area(prettify_label(c), value=str(item.get(c, "")), height=120)

        with st.expander("Editar Treinamento (training_*)", expanded=True):
            for c in training_cols:
                edited[c] = st.text_area(prettify_label(c), value=str(item.get(c, "")), height=120)

        if st.button("Salvar alterações", type="primary", use_container_width=True):
            try:
                items2 = upsert_item(items, edited)
                write_sheet(items_tab, items2)
                st.success("Alterações salvas e sincronizadas com a planilha.")
                time.sleep(0.4)
                st.rerun()
            except Exception as e:
                st.error(f"Falha ao salvar: {e}")


# ======================================================
# START (apenas 1 chamada)
# ======================================================
main()
