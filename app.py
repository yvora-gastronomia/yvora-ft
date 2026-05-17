import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
from gspread.exceptions import APIError


st.set_page_config(
    page_title="Yvora | Fichas Técnicas",
    layout="wide",
    initial_sidebar_state="collapsed",
)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

DEFAULT_SHEET_ID = "1bJVOGJW1zZSN3J64vHT89Dm_GW2ExdmdTtKvQnH7ndw"
DEFAULT_USERS_TAB = "users"
DEFAULT_ITEMS_TAB = "items"

LOGO_CANDIDATES = [
    "Yvora_logo.png",
    "Yvora_logo.jpg",
    "Yvora_logo.jpeg",
    "Yvora_logo.webp",
    "yvora_logo.png",
    "yvora_logo.jpg",
    "yvora_logo.jpeg",
    "yvora_logo.webp",
    "YVORA_logo.png",
    "YVORA_logo.jpg",
    "YVORA_logo.jpeg",
    "YVORA_logo.webp",
]

ROLE_LABEL = {
    "viewer": "Cozinha",
    "editor": "Chefe",
    "admin": "Administrador",
}

REQUIRED_USER_COLS = ["username", "password", "role", "active", "can_drinks", "can_pratos"]
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


st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600&family=DM+Serif+Display&display=swap');

html, body, [class*="css"] {
    font-family: 'DM Sans', sans-serif;
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
.title-bar h1 {
    font-size: 20px;
    margin: 0;
    font-family: 'DM Serif Display', serif;
}
.badge {
    background: rgba(255,255,255,0.15);
    padding: 8px 14px;
    border-radius: 999px;
    font-size: 14px;
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
</style>
""",
    unsafe_allow_html=True,
)


def _get_cfg(name: str, default: str = "") -> str:
    if hasattr(st, "secrets") and name in st.secrets:
        return str(st.secrets[name]).strip()
    if hasattr(st, "secrets") and "app" in st.secrets and name in st.secrets["app"]:
        return str(st.secrets["app"][name]).strip()
    return os.getenv(name, default).strip()


def normalize_sheet_id(value: str) -> str:
    v = str(value or "").strip()
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", v)
    return m.group(1) if m else v


def get_sheet_id() -> str:
    return normalize_sheet_id(_get_cfg("SHEET_ID", DEFAULT_SHEET_ID))


def get_users_tab() -> str:
    return _get_cfg("USERS_TAB", DEFAULT_USERS_TAB)


def get_items_tab() -> str:
    return _get_cfg("ITEMS_TAB", DEFAULT_ITEMS_TAB)


def retryable(fn, tries: int = 6, base_sleep: float = 0.8, max_sleep: float = 10.0):
    last = None
    for i in range(tries):
        try:
            return fn()
        except APIError as e:
            last = e
            msg = str(e)
            is_quota = (
                "429" in msg
                or "Quota exceeded" in msg
                or "RESOURCE_EXHAUSTED" in msg
                or "500" in msg
                or "503" in msg
            )
            if not is_quota and i >= 1:
                raise
            time.sleep(min(max_sleep, base_sleep * (2 ** i)))
    raise last


@st.cache_resource
def gs_client():
    if "gcp_service_account" not in st.secrets:
        raise RuntimeError("Secrets precisa ter [gcp_service_account].")

    info = dict(st.secrets["gcp_service_account"])

    if "private_key" in info:
        info["private_key"] = str(info["private_key"]).replace("\\n", "\n")

    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    return gspread.authorize(creds)


@st.cache_resource
def open_sheet_cached(sheet_id: str):
    client = gs_client()
    return retryable(lambda: client.open_by_key(sheet_id))


def open_sheet():
    return open_sheet_cached(get_sheet_id())


def list_tabs() -> List[str]:
    sh = open_sheet()
    return [ws.title for ws in retryable(lambda: sh.worksheets())]


def pick_tab(candidates: List[str]) -> str:
    titles = list_tabs()
    exact = set(titles)

    for c in candidates:
        if c in exact:
            return c

    lower = {t.lower(): t for t in titles}
    for c in candidates:
        if c.lower() in lower:
            return lower[c.lower()]

    raise RuntimeError(f"Nenhuma aba encontrada. Candidatas: {candidates}. Existentes: {titles}")


def worksheet(tab: str):
    sh = open_sheet()
    return retryable(lambda: sh.worksheet(tab))


def read_all_values(tab: str) -> List[List[str]]:
    ws = worksheet(tab)
    return retryable(lambda: ws.get_all_values())


def to_df(values: List[List[str]]) -> pd.DataFrame:
    if not values:
        return pd.DataFrame()

    header = [str(x).strip() for x in values[0]]
    body = values[1:]
    width = len(header)

    rows = []
    for r in body:
        rows.append(list(r[:width]) + [""] * max(0, width - len(r)))

    df = pd.DataFrame(rows, columns=header)
    df.columns = [str(c).strip() for c in df.columns]
    return df.fillna("")


@st.cache_data(ttl=30)
def read_df_cached(tab: str) -> pd.DataFrame:
    return to_df(read_all_values(tab))


def clear_data_cache():
    read_df_cached.clear()


def find_row_number_by_id(tab: str, item_id: str) -> Optional[int]:
    values = read_all_values(tab)

    if not values:
        return None

    header = [str(x).strip() for x in values[0]]

    if "id" not in header:
        return None

    id_idx = header.index("id")

    for i, row in enumerate(values[1:], start=2):
        current = row[id_idx] if id_idx < len(row) else ""
        if str(current).strip() == str(item_id).strip():
            return i

    return None


def rowcol_to_a1(row: int, col: int) -> str:
    result = ""
    col_num = col
    while col_num:
        col_num, rem = divmod(col_num - 1, 26)
        result = chr(65 + rem) + result
    return f"{result}{row}"


def update_item_row(tab: str, item: Dict[str, str]):
    values = read_all_values(tab)
    if not values:
        raise RuntimeError("A aba de itens está vazia ou sem cabeçalho.")

    header = [str(x).strip() for x in values[0]]
    item_id = str(item.get("id", "")).strip()

    if not item_id:
        raise RuntimeError("ID do item é obrigatório.")

    row_num = find_row_number_by_id(tab, item_id)
    if row_num is None:
        row_num = len(values) + 1

    row_values = [str(item.get(col, "")) for col in header]
    end_a1 = rowcol_to_a1(row_num, len(header))
    ws = worksheet(tab)

    retryable(
        lambda: ws.update(
            range_name=f"A{row_num}:{end_a1}",
            values=[row_values],
            value_input_option="RAW",
        )
    )

    clear_data_cache()


def delete_item_row(tab: str, item_id: str):
    row_num = find_row_number_by_id(tab, item_id)

    if row_num is None:
        raise RuntimeError("Item não encontrado para exclusão.")

    ws = worksheet(tab)
    retryable(lambda: ws.delete_rows(row_num))
    clear_data_cache()


def find_logo_path() -> Optional[str]:
    try:
        base = Path(__file__).parent
    except Exception:
        base = Path(".")

    for name in LOGO_CANDIDATES:
        p = base / name
        if p.exists():
            return str(p)

    assets = base / "assets"
    if assets.exists():
        for name in LOGO_CANDIDATES:
            p = assets / name
            if p.exists():
                return str(p)

    return None


def as_bool(v: Any) -> bool:
    return str(v).strip().lower() in {"1", "true", "sim", "yes", "y", "s", "ativo"}


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

    module_type = str(module_type).strip().lower()

    if module_type == "drink":
        return as_bool(auth.get("can_drinks"))

    return as_bool(auth.get("can_pratos"))


def logout():
    for k in ["auth", "item", "confirm_delete", "creating_new"]:
        st.session_state.pop(k, None)
    clear_data_cache()


def validate_users_df(users: pd.DataFrame):
    missing = [c for c in REQUIRED_USER_COLS if c not in users.columns]
    if missing:
        raise RuntimeError(f"Aba users faltando colunas: {missing}. Colunas atuais: {list(users.columns)}")


def ensure_item_schema(items: pd.DataFrame) -> pd.DataFrame:
    out = items.copy()
    for c in BASE_ITEM_COLS:
        if c not in out.columns:
            out[c] = ""
    return out.fillna("")


def next_id(items: pd.DataFrame, prefix: str) -> str:
    if items.empty or "id" not in items.columns:
        return f"{prefix}001"

    nums = []
    for x in items["id"].astype(str).tolist():
        x = x.strip()
        if x.startswith(prefix):
            tail = x.replace(prefix, "")
            if tail.isdigit():
                nums.append(int(tail))

    n = max(nums) + 1 if nums else 1
    return f"{prefix}{str(n).zfill(3)}"


def prettify_label(col: str) -> str:
    s = str(col).replace("_", " ").strip()
    return s[:1].upper() + s[1:] if s else col


def get_mode_cols(all_cols: List[str], prefix: str) -> List[str]:
    pref = [c for c in all_cols if str(c).startswith(prefix)]
    priority = [
        f"{prefix}ingredients",
        f"{prefix}steps",
        f"{prefix}plating",
        f"{prefix}mise_en_place",
        f"{prefix}details",
        f"{prefix}common_mistakes",
        f"{prefix}quality_check",
    ]

    ordered = []
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
        c
        for c in all_cols
        if c not in gens
        and c not in BASE_ITEM_COLS
        and not str(c).startswith("service_")
        and not str(c).startswith("training_")
    ]
    return gens, sorted(extras)


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


def drive_thumbnail_url(url: str, size: int = 1400) -> Optional[str]:
    fid = extract_drive_file_id(url)
    if not fid:
        return None
    return f"https://drive.google.com/thumbnail?id={fid}&sz=w{size}"


def drive_preview_url(url: str) -> Optional[str]:
    fid = extract_drive_file_id(url)
    if not fid:
        return None
    return f"https://drive.google.com/file/d/{fid}/preview"


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


def render_image_or_media(item: Dict[str, str], all_cols: List[str]):
    raw = str(item.get("cover_photo_url", "")).strip() if "cover_photo_url" in all_cols else ""
    if raw:
        thumb = drive_thumbnail_url(raw)
        try:
            st.image(thumb or raw, use_container_width=True)
        except Exception:
            st.caption("Imagem indisponível.")

    rawv = str(item.get("training_video_url", "")).strip() if "training_video_url" in all_cols else ""
    if rawv:
        yt = extract_youtube_id(rawv)
        if yt:
            try:
                st.video(normalize_youtube_url(rawv))
            except Exception:
                st.caption("Vídeo indisponível.")
        else:
            preview = drive_preview_url(rawv)
            if preview:
                st.markdown(
                    f'<iframe src="{preview}" width="100%" height="420" style="border:none;border-radius:12px;"></iframe>',
                    unsafe_allow_html=True,
                )
            else:
                try:
                    st.video(rawv)
                except Exception:
                    st.caption("Vídeo indisponível.")


def header():
    auth = st.session_state.get("auth")
    user_text = "Acesso"

    if auth:
        role = auth.get("role", "")
        user_text = f"{ROLE_LABEL.get(role, role)} | {auth.get('username', '')}"

    st.markdown(
        f"""
        <div class="title-bar">
            <h1>Yvora · Fichas Técnicas</h1>
            <div class="badge">{user_text}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    lp = find_logo_path()
    if lp:
        colA, _ = st.columns([1, 3])
        with colA:
            try:
                st.image(lp, use_container_width=True)
            except Exception:
                pass

    if auth:
        _, _, col3 = st.columns([2, 2, 2])
        with col3:
            st.markdown('<div class="small-btn">', unsafe_allow_html=True)
            if st.button("Trocar usuário", use_container_width=True):
                logout()
                st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)


def login(users: pd.DataFrame):
    validate_users_df(users)

    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.subheader("Login")

    with st.form("login_form", clear_on_submit=False):
        u = st.text_input("Usuário")
        p = st.text_input("Senha", type="password")

        c1, c2 = st.columns(2)
        entrar = c1.form_submit_button("Entrar", type="primary", use_container_width=True)
        limpar = c2.form_submit_button("Limpar", use_container_width=True)

    if limpar:
        st.rerun()

    if entrar:
        df = users.copy()

        for c in REQUIRED_USER_COLS:
            if c in df.columns:
                df[c] = df[c].astype(str).str.strip()

        match = df[
            (df["username"] == str(u).strip())
            & (df["password"] == str(p).strip())
            & (df["active"].apply(as_bool))
        ]

        if match.empty:
            st.error("Usuário ou senha inválidos, ou usuário inativo.")
        else:
            row = match.iloc[0]
            st.session_state["auth"] = {
                "username": str(row["username"]),
                "role": str(row["role"]).strip().lower(),
                "can_drinks": str(row["can_drinks"]),
                "can_pratos": str(row["can_pratos"]),
            }
            st.session_state.pop("item", None)
            st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)


def render_text_sections(item: Dict[str, str], cols: List[str]):
    shown = False

    for c in cols:
        val = str(item.get(c, "")).strip()
        if val:
            shown = True
            st.markdown(f"### {prettify_label(c)}")
            st.text(val)

    if not shown:
        st.info("Sem informações preenchidas neste modo.")


def admin_or_editor_form(item: Dict[str, str], all_cols: List[str], items_tab: str):
    role = st.session_state.get("auth", {}).get("role")

    if role == "admin":
        title = "Administrador · Gerenciar item"
    else:
        title = "Chefe · Editar conteúdo"

    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.subheader(title)

    with st.form(f"edit_form_{item.get('id', '')}"):
        edited = dict(item)

        if role == "admin":
            c1, c2 = st.columns(2)

            with c1:
                current_type = str(item.get("type", "")).strip().lower()
                edited["type"] = st.selectbox(
                    "Tipo",
                    ["drink", "prato"],
                    index=0 if current_type == "drink" else 1,
                )

                if "category" in all_cols:
                    edited["category"] = st.text_input("Categoria", value=str(item.get("category", "")))

            with c2:
                st.text_input("ID", value=str(item.get("id", "")), disabled=True)
                edited["id"] = str(item.get("id", ""))
                edited["name"] = st.text_input("Título", value=str(item.get("name", "")))

        for c in ["concept", "strategy", "tags", "yield", "total_time_min", "cover_photo_url", "training_video_url"]:
            if c in all_cols:
                if c in ["concept", "strategy"]:
                    edited[c] = st.text_area(prettify_label(c), value=str(item.get(c, "")), height=100)
                else:
                    edited[c] = st.text_input(prettify_label(c), value=str(item.get(c, "")))

        st.markdown("<hr/>", unsafe_allow_html=True)

        service_cols = get_mode_cols(all_cols, "service_")
        training_cols = get_mode_cols(all_cols, "training_")

        with st.expander("Campos de Serviço", expanded=True):
            for c in service_cols:
                edited[c] = st.text_area(prettify_label(c), value=str(item.get(c, "")), height=120)

        with st.expander("Campos de Treinamento", expanded=True):
            for c in training_cols:
                edited[c] = st.text_area(prettify_label(c), value=str(item.get(c, "")), height=120)

        c1, c2 = st.columns([2, 1])
        save = c1.form_submit_button("Salvar", type="primary", use_container_width=True)
        delete = False
        if role == "admin":
            delete = c2.form_submit_button("Excluir", use_container_width=True)

    if save:
        try:
            if not str(edited.get("name", "")).strip():
                st.error("O título é obrigatório.")
                st.markdown("</div>", unsafe_allow_html=True)
                return

            update_item_row(items_tab, edited)
            st.toast("Salvo com sucesso.")
            st.rerun()
        except Exception as e:
            st.error(f"Falha ao salvar: {e}")

    if delete and role == "admin":
        st.session_state["confirm_delete"] = True

    if st.session_state.get("confirm_delete") and role == "admin":
        st.warning("Confirme a exclusão definitiva deste item.")
        c1, c2 = st.columns(2)

        if c1.button("Confirmar exclusão", type="primary", use_container_width=True):
            try:
                delete_item_row(items_tab, str(item.get("id", "")))
                st.session_state.pop("confirm_delete", None)
                st.session_state.pop("item", None)
                st.toast("Item excluído.")
                st.rerun()
            except Exception as e:
                st.error(f"Falha ao excluir: {e}")

        if c2.button("Cancelar", use_container_width=True):
            st.session_state.pop("confirm_delete", None)
            st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)


def render_item(item: Dict[str, str], all_cols: List[str], items_tab: str):
    title = str(item.get("name", "")).strip() or "Item sem nome"
    category = str(item.get("category", "")).strip()
    item_type = str(item.get("type", "")).strip()

    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.subheader(title)

    cap = " · ".join([x for x in [category, "Drink" if item_type == "drink" else "Prato"] if x])
    if cap:
        st.caption(cap)

    general_cols, extra_cols = get_general_cols(all_cols)

    left, right = st.columns([1.3, 1])

    with left:
        for c in general_cols:
            if c in ["name", "cover_photo_url", "training_video_url"]:
                continue
            val = str(item.get(c, "")).strip()
            if val:
                st.markdown(f"### {prettify_label(c)}")
                st.write(val)

        visible_extras = [c for c in extra_cols if str(item.get(c, "")).strip()]
        if visible_extras:
            with st.expander("Outras informações", expanded=False):
                for c in visible_extras:
                    st.markdown(f"**{prettify_label(c)}**")
                    st.write(str(item.get(c, "")).strip())

    with right:
        render_image_or_media(item, all_cols)

    st.markdown("</div>", unsafe_allow_html=True)

    mode = st.radio(
        "Modo de uso",
        ["Serviço", "Treinamento"],
        horizontal=True,
        key=f"mode_{item.get('id', '')}",
    )

    st.markdown("<div class='card'>", unsafe_allow_html=True)
    if mode == "Serviço":
        render_text_sections(item, get_mode_cols(all_cols, "service_"))
    else:
        render_text_sections(item, get_mode_cols(all_cols, "training_"))
    st.markdown("</div>", unsafe_allow_html=True)

    if can_edit():
        admin_or_editor_form(item, all_cols, items_tab)


def create_item_panel(items: pd.DataFrame, items_tab: str):
    if not is_admin():
        return

    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.subheader("Administrador · Criar nova ficha")

    c1, c2 = st.columns([1, 2])
    with c1:
        tipo = st.selectbox(
            "Tipo da nova ficha",
            ["prato", "drink"],
            format_func=lambda x: "Prato" if x == "prato" else "Drink",
            key="novo_tipo",
        )
    with c2:
        criar = st.button("Criar nova ficha", type="primary", use_container_width=True)

    st.markdown("</div>", unsafe_allow_html=True)

    if criar:
        try:
            all_cols = list(items.columns)
            prefix = "P" if tipo == "prato" else "D"
            new_id = next_id(items, prefix)

            new_item = {c: "" for c in all_cols}
            new_item["id"] = new_id
            new_item["type"] = tipo
            new_item["name"] = "Nova ficha"

            update_item_row(items_tab, new_item)
            st.session_state["item"] = new_id
            st.toast("Nova ficha criada.")
            st.rerun()
        except Exception as e:
            st.error(f"Falha ao criar nova ficha: {e}")


def select_item_screen(items: pd.DataFrame, items_tab: str):
    items = ensure_item_schema(items)

    if items.empty:
        st.warning("Nenhum item encontrado.")
        create_item_panel(items, items_tab)
        return

    for c in ["id", "type", "name"]:
        items[c] = items[c].astype(str).str.strip()

    available = items[items["type"].apply(lambda x: has_access(x))].copy()

    if available.empty:
        st.warning("Seu usuário não possui acesso às fichas cadastradas.")
        return

    tipo_opcoes = []
    if any(available["type"] == "prato"):
        tipo_opcoes.append("Pratos")
    if any(available["type"] == "drink"):
        tipo_opcoes.append("Drinks")
    if not tipo_opcoes:
        tipo_opcoes = ["Todos"]

    st.markdown("<div class='card'>", unsafe_allow_html=True)
    c1, c2 = st.columns([1, 2])

    with c1:
        filtro_tipo = st.selectbox("Categoria", tipo_opcoes, key="filtro_tipo")

    with c2:
        busca = st.text_input("Buscar ficha", placeholder="Nome, categoria, tag ou ID", key="busca_item")

    filtered = available.copy()

    if filtro_tipo == "Pratos":
        filtered = filtered[filtered["type"] == "prato"]
    elif filtro_tipo == "Drinks":
        filtered = filtered[filtered["type"] == "drink"]

    if busca:
        b = busca.strip().lower()

        def row_matches(row):
            fields = [
                str(row.get("name", "")),
                str(row.get("category", "")),
                str(row.get("tags", "")),
                str(row.get("id", "")),
            ]
            return b in " ".join(fields).lower()

        filtered = filtered[filtered.apply(row_matches, axis=1)]

    if filtered.empty:
        st.info("Nenhum item encontrado com os filtros atuais.")
        st.markdown("</div>", unsafe_allow_html=True)
        create_item_panel(items, items_tab)
        return

    labels = []
    id_by_label = {}

    for _, row in filtered.iterrows():
        name = str(row.get("name", "")).strip() or "Sem nome"
        item_id = str(row.get("id", "")).strip()
        category = str(row.get("category", "")).strip()
        tipo = "Drink" if str(row.get("type", "")).strip() == "drink" else "Prato"

        extra = " · ".join([x for x in [tipo, category, item_id] if x])
        label = f"{name} ({extra})" if extra else name

        labels.append(label)
        id_by_label[label] = item_id

    current_id = st.session_state.get("item")
    default_index = 0

    if current_id:
        for i, label in enumerate(labels):
            if id_by_label[label] == current_id:
                default_index = i
                break

    selected_label = st.selectbox(
        "Selecione a ficha técnica",
        labels,
        index=default_index,
        key="selected_item_label",
    )

    selected_id = id_by_label[selected_label]
    st.session_state["item"] = selected_id

    st.markdown("</div>", unsafe_allow_html=True)

    selected = filtered[filtered["id"] == selected_id]
    if selected.empty:
        st.error("Item selecionado não encontrado.")
        return

    all_cols = list(items.columns)
    item = {c: str(selected.iloc[0].get(c, "")) for c in all_cols}

    render_item(item, all_cols, items_tab)
    create_item_panel(items, items_tab)


def diagnostics_panel():
    with st.expander("Diagnóstico técnico", expanded=False):
        st.write("Configuração atual")
        st.caption(f"SHEET_ID: {get_sheet_id()}")
        st.caption(f"USERS_TAB: {get_users_tab()}")
        st.caption(f"ITEMS_TAB: {get_items_tab()}")

        c1, c2, c3 = st.columns(3)

        with c1:
            if st.button("Testar Google Sheets", use_container_width=True):
                try:
                    tabs = list_tabs()
                    st.success("Conexão OK.")
                    st.write(tabs)
                except Exception as e:
                    st.error(f"Falha na conexão: {e}")

        with c2:
            if st.button("Limpar cache", use_container_width=True):
                clear_data_cache()
                st.cache_resource.clear()
                st.success("Cache limpo.")

        with c3:
            if st.button("Recarregar app", use_container_width=True):
                st.rerun()


def main():
    header()

    users_tab = get_users_tab()
    items_tab = get_items_tab()

    try:
        users = read_df_cached(users_tab)

        if "auth" not in st.session_state:
            login(users)
            diagnostics_panel()
            st.stop()

        items = read_df_cached(items_tab)
        select_item_screen(items, items_tab)
        diagnostics_panel()

    except Exception as e:
        st.error("Falha ao carregar o app.")
        st.exception(e)
        diagnostics_panel()
        st.stop()


if __name__ == "__main__":
    main()
