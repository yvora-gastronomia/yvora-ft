import re
import json
import time
import random
from pathlib import Path
from typing import Optional, List, Dict, Tuple

import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


# ======================================================
# CONFIG
# ======================================================
st.set_page_config(
    page_title="Yvora | Fichas Técnicas",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ======================================================
# ESTILO YVORA
# ======================================================
st.markdown(
    """
<style>
html, body, [class*="css"] {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial;
}
.stApp {
    background-color: #EFE7DD;
}
.block-container {
    max-width: 1200px;
    padding-top: 1rem;
}
.card {
    background: white;
    border-radius: 18px;
    padding: 16px;
    margin-bottom: 16px;
    box-shadow: 0 6px 20px rgba(0,0,0,0.06);
}
.title-bar {
    background: #0E2A47;
    color: white;
    padding: 14px 18px;
    border-radius: 18px;
    margin-bottom: 16px;
    display: flex;
    justify-content: space-between;
    align-items: center;
}
.title-left {
    display: flex;
    align-items: center;
    gap: 12px;
}
.title-bar h1 {
    font-size: 20px;
    margin: 0;
}
.badge {
    background: rgba(255,255,255,0.15);
    padding: 8px 14px;
    border-radius: 999px;
    font-size: 14px;
    display: flex;
    gap: 10px;
    align-items: center;
}
.stButton > button {
    border-radius: 14px;
    font-size: 16px;
    padding: 12px;
}
.stButton > button[kind="primary"] {
    background-color: #0E2A47;
}
.small-btn > button {
    padding: 8px 10px !important;
    font-size: 14px !important;
    border-radius: 12px !important;
}
hr {
    border: none;
    border-top: 1px solid rgba(0,0,0,0.08);
    margin: 10px 0;
}
.muted {
    color: rgba(0,0,0,0.55);
    font-size: 12px;
}
.section-title {
    font-weight: 700;
    font-size: 18px;
    margin-bottom: 8px;
}
</style>
""",
    unsafe_allow_html=True,
)

# ======================================================
# LOGO
# ======================================================
LOGO_CANDIDATES = [
    "Yvora_logo.png", "Yvora_logo.jpg", "Yvora_logo.jpeg", "Yvora_logo.webp",
    "yvora_logo.png", "yvora_logo.jpg", "yvora_logo.jpeg", "yvora_logo.webp",
    "Ivora_logo.png", "Ivora_logo.jpg", "Ivora_logo.jpeg", "Ivora_logo.webp",
]


def find_logo_path() -> Optional[str]:
    base = Path(__file__).parent
    for name in LOGO_CANDIDATES:
        p = base / name
        if p.exists():
            return str(p)
    return None


# ======================================================
# HELPERS URL
# ======================================================
def extract_drive_file_id(url: str) -> Optional[str]:
    if not url:
        return None
    u = str(url).strip()

    patterns = [
        r"/file/d/([a-zA-Z0-9_-]+)",
        r"[?&]id=([a-zA-Z0-9_-]+)",
        r"/uc\?.*id=([a-zA-Z0-9_-]+)",
        r"/d/([a-zA-Z0-9_-]+)",
    ]
    for pattern in patterns:
        m = re.search(pattern, u)
        if m:
            return m.group(1)

    return None


def normalize_drive_direct_view(url: str) -> str:
    fid = extract_drive_file_id(url)
    if not fid:
        return url
    return f"https://drive.google.com/uc?export=view&id={fid}"


def drive_preview_url(url: str) -> Optional[str]:
    fid = extract_drive_file_id(url)
    if not fid:
        return None
    return f"https://drive.google.com/file/d/{fid}/preview"


def drive_thumbnail_url(url: str, size: int = 1400) -> Optional[str]:
    fid = extract_drive_file_id(url)
    if not fid:
        return None
    return f"https://drive.google.com/thumbnail?id={fid}&sz=w{size}"


def extract_youtube_id(url: str) -> Optional[str]:
    if not url:
        return None
    u = str(url).strip()

    patterns = [
        r"youtu\.be/([a-zA-Z0-9_-]{6,})",
        r"[?&]v=([a-zA-Z0-9_-]{6,})",
        r"youtube\.com/shorts/([a-zA-Z0-9_-]{6,})",
        r"youtube\.com/embed/([a-zA-Z0-9_-]{6,})",
    ]
    for pattern in patterns:
        m = re.search(pattern, u)
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
    return build("sheets", "v4", credentials=get_creds(), cache_discovery=False)


def gs_call(fn, *args, retries: int = 5, base_sleep: float = 0.8, **kwargs):
    last_exc = None

    for attempt in range(retries):
        try:
            return fn(*args, **kwargs).execute()
        except HttpError as e:
            last_exc = e
            status = getattr(e.resp, "status", None)
            if status in (429, 500, 502, 503, 504):
                sleep_s = base_sleep * (2 ** attempt) + random.uniform(0, 0.35)
                time.sleep(sleep_s)
                continue
            raise
        except Exception as e:
            last_exc = e
            if attempt < retries - 1:
                sleep_s = base_sleep * (2 ** attempt) + random.uniform(0, 0.35)
                time.sleep(sleep_s)
                continue
            raise

    raise last_exc


def col_to_a1(col_num: int) -> str:
    result = ""
    while col_num:
        col_num, rem = divmod(col_num - 1, 26)
        result = chr(65 + rem) + result
    return result


@st.cache_data(ttl=60)
def get_sheet_metadata(spreadsheet_id: str) -> dict:
    return gs_call(
        sheets_service().spreadsheets().get,
        spreadsheetId=spreadsheet_id,
        fields="sheets(properties(sheetId,title))",
    )


def get_sheet_id_by_title(spreadsheet_id: str, title: str) -> Optional[int]:
    meta = get_sheet_metadata(spreadsheet_id)
    for sh in meta.get("sheets", []):
        props = sh.get("properties", {})
        if props.get("title") == title:
            return int(props.get("sheetId"))
    return None


@st.cache_data(ttl=20)
def read_sheet_values_fast(tab: str) -> pd.DataFrame:
    ssid = st.secrets["SHEET_ID"]
    result = gs_call(
        sheets_service().spreadsheets().values().get,
        spreadsheetId=ssid,
        range=tab,
    )

    values = result.get("values", [])
    if not values:
        return pd.DataFrame()

    header = values[0]
    rows = values[1:]

    width = len(header)
    normalized_rows = []
    for r in rows:
        row = list(r[:width]) + [""] * max(0, width - len(r))
        normalized_rows.append(row)

    return pd.DataFrame(normalized_rows, columns=header)


@st.cache_data(ttl=20)
def get_header_and_rows(tab: str) -> Tuple[List[str], List[List[str]]]:
    ssid = st.secrets["SHEET_ID"]
    result = gs_call(
        sheets_service().spreadsheets().values().get,
        spreadsheetId=ssid,
        range=tab,
    )
    values = result.get("values", [])
    if not values:
        return [], []
    header = values[0]
    rows = values[1:]
    return header, rows


def clear_sheet_caches():
    read_sheet_values_fast.clear()
    get_header_and_rows.clear()
    get_sheet_metadata.clear()


def find_row_number_by_id(tab: str, item_id: str) -> Optional[int]:
    header, rows = get_header_and_rows(tab)
    if not header or "id" not in header:
        return None

    id_idx = header.index("id")
    for i, row in enumerate(rows, start=2):
        current = row[id_idx] if id_idx < len(row) else ""
        if str(current).strip() == str(item_id).strip():
            return i
    return None


def update_item_row(tab: str, item: Dict[str, str]):
    ssid = st.secrets["SHEET_ID"]
    header, rows = get_header_and_rows(tab)

    if not header:
        raise ValueError("A aba está vazia ou sem cabeçalho.")

    item_id = str(item.get("id", "")).strip()
    if not item_id:
        raise ValueError("ID do item é obrigatório.")

    row_num = find_row_number_by_id(tab, item_id)
    if row_num is None:
        row_num = len(rows) + 2

    row_values = [str(item.get(col, "")) for col in header]
    end_col = col_to_a1(len(header))
    target_range = f"{tab}!A{row_num}:{end_col}{row_num}"

    gs_call(
        sheets_service().spreadsheets().values().update,
        spreadsheetId=ssid,
        range=target_range,
        valueInputOption="RAW",
        body={"values": [row_values]},
    )

    clear_sheet_caches()


def delete_item_row(tab: str, item_id: str):
    ssid = st.secrets["SHEET_ID"]
    row_num = find_row_number_by_id(tab, item_id)
    if row_num is None:
        raise ValueError("Item não encontrado para exclusão.")

    sheet_id = get_sheet_id_by_title(ssid, tab)
    if sheet_id is None:
        raise ValueError("Não foi possível localizar a aba para exclusão.")

    start_index = row_num - 1
    end_index = row_num

    gs_call(
        sheets_service().spreadsheets().batchUpdate,
        spreadsheetId=ssid,
        body={
            "requests": [
                {
                    "deleteDimension": {
                        "range": {
                            "sheetId": sheet_id,
                            "dimension": "ROWS",
                            "startIndex": start_index,
                            "endIndex": end_index,
                        }
                    }
                }
            ]
        },
    )

    clear_sheet_caches()


# ======================================================
# AUTH
# ======================================================
ROLE_LABEL = {
    "viewer": "Cozinha",
    "editor": "Chefe",
    "admin": "Administrador",
}

REQUIRED_USER_COLS = ["username", "password", "role", "active", "can_drinks", "can_pratos"]


def logout():
    for k in [
        "auth",
        "item",
        "login_user",
        "login_pass",
        "confirm_delete",
        "creating_new",
    ]:
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

    with st.form("login_form"):
        u = st.text_input("Usuário", key="login_user")
        p = st.text_input("Senha", type="password", key="login_pass")
        c1, c2 = st.columns(2)
        entrar = c1.form_submit_button("Entrar", type="primary", use_container_width=True)
        limpar = c2.form_submit_button("Limpar", use_container_width=True)

    if limpar:
        st.session_state["login_user"] = ""
        st.session_state["login_pass"] = ""
        st.rerun()

    if entrar:
        df = users.copy()
        for c in ["active", "can_drinks", "can_pratos"]:
            df[c] = df[c].astype(str)

        match = df[
            (df["username"].astype(str) == str(u)) &
            (df["password"].astype(str) == str(p)) &
            (df["active"] == "1")
        ]

        if match.empty:
            st.error("Usuário ou senha inválidos, ou usuário inativo.")
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

    st.markdown("</div>", unsafe_allow_html=True)


# ======================================================
# HEADER
# ======================================================
def header():
    auth = st.session_state.get("auth")
    user_text = "Acesso"

    if auth:
        role = auth.get("role", "")
        user_text = f"{ROLE_LABEL.get(role, role)} | {auth.get('username', '')}"

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
    nums: List[int] = []
    for x in ids:
        if x.startswith(prefix):
            tail = x.replace(prefix, "")
            if tail.isdigit():
                nums.append(int(tail))

    n = max(nums) + 1 if nums else 1
    return f"{prefix}{str(n).zfill(3)}"


def prettify_label(col: str) -> str:
    s = col.replace("_", " ").strip()
    return s[:1].upper() + s[1:] if s else col


def get_mode_cols(all_cols: List[str], prefix: str) -> List[str]:
    pref = [c for c in all_cols if c.startswith(prefix)]
    priority = [
        f"{prefix}ingredients",
        f"{prefix}steps",
        f"{prefix}plating",
        f"{prefix}mise_en_place",
        f"{prefix}details",
        f"{prefix}common_mistakes",
    ]
    ordered: List[str] = []
    for p in priority:
        if p in pref:
            ordered.append(p)
    for c in sorted(pref):
        if c not in ordered:
            ordered.append(c)
    return ordered


def get_general_cols(all_cols: List[str]) -> Tuple[List[str], List[str]]:
    gens = [c for c in PREFERRED_GENERAL_ORDER if c in all_cols]
    extras = [
        c for c in all_cols
        if c not in gens
        and c not in BASE_ITEM_COLS
        and not c.startswith("service_")
        and not c.startswith("training_")
    ]
    return gens, sorted(extras)


def render_text_sections(item: Dict[str, str], cols: List[str]):
    any_shown = False
    for c in cols:
        val = str(item.get(c, "")).strip()
        if val:
            any_shown = True
            st.markdown(f"### {prettify_label(c)}")
            st.text(val)

    if not any_shown:
        st.info("Sem informações preenchidas neste modo.")


def render_media(item: Dict[str, str], all_cols: List[str]):
    if "cover_photo_url" in all_cols:
        raw = str(item.get("cover_photo_url", "")).strip()
        if raw:
            thumb = drive_thumbnail_url(raw)
            if thumb:
                st.image(thumb, use_container_width=True)
            else:
                st.image(raw, use_container_width=True)

    if "training_video_url" in all_cols:
        rawv = str(item.get("training_video_url", "")).strip()
        if rawv:
            yt_id = extract_youtube_id(rawv)
            if yt_id:
                st.video(normalize_youtube_url(rawv))
            else:
                preview = drive_preview_url(rawv)
                if preview:
                    st.components.v1.iframe(preview, height=420)
                else:
                    st.video(rawv)


def build_item_from_row(row: pd.Series, all_cols: List[str]) -> Dict[str, str]:
    item = {}
    for c in all_cols:
        item[c] = str(row.get(c, ""))
    return item


# ======================================================
# FORMS
# ======================================================
def admin_item_form(item: Dict[str, str], all_cols: List[str], tipo_val: str, items_tab: str, creating_new: bool):
    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.subheader("Administrador · Gerenciar item")

    with st.form("admin_item_form"):
        edited = dict(item)

        col1, col2 = st.columns([1, 1])

        with col1:
            edited["type"] = st.selectbox(
                "Tipo",
                ["drink", "prato"],
                index=0 if str(item.get("type", tipo_val)) == "drink" else 1,
            )
            if "category" in all_cols:
                edited["category"] = st.text_input("Categoria", value=str(item.get("category", "")))
            if "yield" in all_cols:
                edited["yield"] = st.text_input("Rendimento", value=str(item.get("yield", "")))

        with col2:
            st.text_input("ID", value=str(item.get("id", "")), disabled=True)
            edited["id"] = str(item.get("id", ""))
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

        c1, c2 = st.columns([2, 1])
        save_clicked = c1.form_submit_button("Salvar", type="primary", use_container_width=True)
        delete_clicked = False
        if not creating_new:
            delete_clicked = c2.form_submit_button("Excluir", use_container_width=True)

    if save_clicked:
        try:
            if not str(edited.get("name", "")).strip():
                st.error("O título do item é obrigatório.")
                st.markdown("</div>", unsafe_allow_html=True)
                return

            update_item_row(items_tab, edited)
            st.session_state["creating_new"] = False
            st.toast("Salvo com sucesso.")
            st.rerun()
        except Exception as e:
            st.error(f"Falha ao salvar: {e}")

    if delete_clicked and not creating_new:
        st.session_state["confirm_delete"] = True

    if st.session_state.get("confirm_delete") and not creating_new:
        st.warning("Confirme a exclusão definitiva deste item.")
        c1, c2 = st.columns(2)

        if c1.button("Confirmar exclusão", type="primary", use_container_width=True):
            try:
                delete_item_row(items_tab, str(item.get("id", "")))
                st.session_state.pop("confirm_delete", None)
                st.session_state.pop("item", None)
                st.toast("Item excluído com sucesso.")
                st.rerun()
            except Exception as e:
                st.error(f"Falha ao excluir: {e}")

        if c2.button("Cancelar", use_container_width=True):
            st.session_state.pop("confirm_delete", None)
            st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)


def editor_item_form(item: Dict[str, str], all_cols: List[str], items_tab: str):
    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.subheader("Chefe · Editar conteúdo")

    with st.form("editor_item_form"):
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
        with st.expander("Editar Serviço (service_*)", expanded=True):
            for c in service_cols:
                edited[c] = st.text_area(prettify_label(c), value=str(item.get(c, "")), height=120)

        training_cols = get_mode_cols(all_cols, "training_")
        with st.expander("Editar Treinamento (training_*)", expanded=True):
            for c in training_cols:
                edited[c] = st.text_area(prettify_label(c), value=str(item.get(c, "")), height=120)

        submitted = st.form_submit_button("Salvar alterações", type="primary", use_container_width=True)

    if submitted:
        try:
            update_item_row(items_tab, edited)
            st.toast("Alterações salvas com sucesso.")
            st.rerun()
        except Exception as e:
            st.error(f"Falha ao salvar: {e}")

    st.markdown("</div>", unsafe_allow_html=True)


# ======================================================
# MAIN
# ======================================================
def main():
    users_tab = st.secrets.get("USERS_TAB", "users")
    items_tab = st.secrets.get("ITEMS_TAB", "items")

    try:
        users = read_sheet_values_fast(users_tab)
    except Exception as e:
        st.error(f"Erro lendo aba users: {e}")
        return

    header()

    if "auth" not in st.session_state:
        try:
            login(users)
        except Exception as e:
            st.error(str(e))
        return

    try:
        items = read_sheet_values_fast(items_tab)
        items = ensure_item_min_schema(items)
    except Exception as e:
        st.error(f"Erro lendo aba items: {e}")
        return

    auth = st.session_state["auth"]

    allowed_modules: List[str] = []
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
                st.session_state.pop("confirm_delete", None)
                st.rerun()

    tipo_val = "drink" if tipo == "Drinks" else "prato"
    if not has_access(tipo_val):
        st.error("Sem permissão para acessar este módulo.")
        return

    df = items[items["type"].astype(str).str.lower() == tipo_val].copy()

    if busca and not df.empty:
        b = busca.strip().lower()
        name_ok = (
            df["name"].astype(str).str.lower().str.contains(b, regex=False)
            if "name" in df.columns else False
        )
        tags_ok = (
            df["tags"].astype(str).str.lower().str.contains(b, regex=False)
            if "tags" in df.columns else False
        )
        df = df[name_ok | tags_ok]

    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.subheader("Itens")

    if df.empty:
        st.info("Nenhum item encontrado.")
    else:
        show = df.sort_values("name" if "name" in df.columns else "id")
        for _, row in show.iterrows():
            item_id = str(row.get("id", "")).strip()
            label = str(row.get("name", item_id)).strip() or item_id
            if st.button(label, use_container_width=True, key=f"btn_{item_id}"):
                st.session_state["item"] = item_id
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

        item = build_item_from_row(match.iloc[0], all_cols)
        if str(item.get("type", "")).lower().strip() != tipo_val:
            st.session_state.pop("item", None)
            st.warning("O item selecionado não pertence ao módulo atual.")
            st.rerun()

    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.subheader("Novo item" if creating_new else str(item.get("name", "")))

    render_media(item, all_cols)

    meta_parts: List[str] = []
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

    if is_admin():
        admin_item_form(item, all_cols, tipo_val, items_tab, creating_new)
    elif can_edit():
        editor_item_form(item, all_cols, items_tab)


# ======================================================
# START
# ======================================================
if __name__ == "__main__":
    main()