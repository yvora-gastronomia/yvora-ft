import re
import time
from pathlib import Path
from typing import Optional, List, Dict, Tuple, Any

import pandas as pd
import streamlit as st


st.set_page_config(
    page_title="Yvora | Fichas Técnicas",
    layout="wide",
    initial_sidebar_state="collapsed",
)

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
.title-left {
    display: flex;
    align-items: center;
    gap: 12px;
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
</style>
""",
    unsafe_allow_html=True,
)


LOGO_CANDIDATES = [
    "Yvora_logo.png", "Yvora_logo.jpg", "Yvora_logo.jpeg", "Yvora_logo.webp",
    "yvora_logo.png", "yvora_logo.jpg", "yvora_logo.jpeg", "yvora_logo.webp",
    "YVORA_logo.png", "YVORA_logo.jpg", "YVORA_logo.jpeg", "YVORA_logo.webp",
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

CACHE_TTL_SECONDS = 30


def load_gspread():
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        return gspread, Credentials
    except Exception as e:
        st.error("Falha ao carregar dependências Google.")
        st.caption(
            "O ambiente do Streamlit Cloud não instalou gspread/google-auth corretamente. "
            "Atualize requirements.txt, faça Clear cache e depois Reboot app."
        )
        st.exception(e)
        st.stop()


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


def secret_get(key: str, default=None):
    try:
        return st.secrets[key]
    except Exception:
        return default


def nested_secret_get(path: List[str], default=None):
    cur = st.secrets
    try:
        for part in path:
            cur = cur[part]
        return cur
    except Exception:
        return default


def get_sheet_id() -> str:
    sheet_id = secret_get("SHEET_ID")
    if sheet_id:
        return str(sheet_id).strip()

    sheet_id = nested_secret_get(["gsheets", "sheet_id"])
    if sheet_id:
        return str(sheet_id).strip()

    raise ValueError("SHEET_ID não encontrado nos secrets.")


def get_users_tab() -> str:
    return str(secret_get("USERS_TAB", "users")).strip()


def get_items_tab() -> str:
    return str(secret_get("ITEMS_TAB", "items")).strip()


def get_gcp_service_account_dict() -> dict:
    gcp = secret_get("gcp_service_account")
    if gcp is None:
        raise ValueError("gcp_service_account não encontrado nos secrets.")

    try:
        gcp_dict = dict(gcp)
    except Exception:
        raise ValueError("gcp_service_account inválido. Deve ser um bloco TOML válido.")

    if "private_key" in gcp_dict:
        gcp_dict["private_key"] = str(gcp_dict["private_key"]).replace("\\n", "\n")

    return gcp_dict


def validate_runtime_config() -> List[str]:
    errors = []

    try:
        _ = get_sheet_id()
    except Exception as e:
        errors.append(str(e))

    try:
        gcp = get_gcp_service_account_dict()
        required = [
            "type",
            "project_id",
            "private_key_id",
            "private_key",
            "client_email",
            "token_uri",
        ]
        missing = [k for k in required if not gcp.get(k)]
        if missing:
            errors.append("gcp_service_account incompleto. Faltam: " + ", ".join(missing))
    except Exception as e:
        errors.append(str(e))

    return errors


def get_gspread_client():
    if "gspread_client" not in st.session_state:
        gspread, Credentials = load_gspread()
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive.readonly",
        ]
        creds = Credentials.from_service_account_info(
            get_gcp_service_account_dict(),
            scopes=scopes,
        )
        st.session_state["gspread_client"] = gspread.authorize(creds)

    return st.session_state["gspread_client"]


def get_spreadsheet():
    if "spreadsheet" not in st.session_state:
        client = get_gspread_client()
        st.session_state["spreadsheet"] = client.open_by_key(get_sheet_id())

    return st.session_state["spreadsheet"]


def get_worksheet(tab: str):
    return get_spreadsheet().worksheet(tab)


def _sheet_cache_key(tab: str) -> str:
    return f"sheet_cache_{tab}"


def _sheet_cache_time_key(tab: str) -> str:
    return f"sheet_cache_time_{tab}"


def clear_sheet_caches():
    keys_to_del = [k for k in st.session_state if k.startswith("sheet_cache_")]
    for k in keys_to_del:
        del st.session_state[k]


def read_sheet_values_fast(tab: str) -> pd.DataFrame:
    now = time.time()
    ck = _sheet_cache_key(tab)
    tk = _sheet_cache_time_key(tab)

    if ck in st.session_state:
        cached_at = st.session_state.get(tk, 0)
        if now - cached_at < CACHE_TTL_SECONDS:
            return st.session_state[ck]

    ws = get_worksheet(tab)
    values = ws.get_all_values()

    if not values:
        df = pd.DataFrame()
        st.session_state[ck] = df
        st.session_state[tk] = now
        return df

    header = [str(x).strip() for x in values[0]]
    rows = values[1:]
    width = len(header)

    normalized_rows = []
    for r in rows:
        row = list(r[:width]) + [""] * max(0, width - len(r))
        normalized_rows.append(row)

    df = pd.DataFrame(normalized_rows, columns=header)
    df.columns = [str(c).strip() for c in df.columns]
    df = df.fillna("")

    st.session_state[ck] = df
    st.session_state[tk] = now

    return df


def get_header_and_rows(tab: str) -> Tuple[List[str], List[List[str]]]:
    ws = get_worksheet(tab)
    values = ws.get_all_values()

    if not values:
        return [], []

    header = [str(x).strip() for x in values[0]]
    rows = values[1:]

    return header, rows


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
    ws = get_worksheet(tab)
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
    end_col = len(header)

    ws.update(
        range_name=f"A{row_num}:{rowcol_to_a1(row_num, end_col).replace(str(row_num), '')}{row_num}",
        values=[row_values],
        value_input_option="RAW",
    )

    clear_sheet_caches()


def rowcol_to_a1(row: int, col: int) -> str:
    result = ""
    col_num = col
    while col_num:
        col_num, rem = divmod(col_num - 1, 26)
        result = chr(65 + rem) + result
    return f"{result}{row}"


def delete_item_row(tab: str, item_id: str):
    ws = get_worksheet(tab)
    row_num = find_row_number_by_id(tab, item_id)

    if row_num is None:
        raise ValueError("Item não encontrado para exclusão.")

    ws.delete_rows(row_num)

    clear_sheet_caches()


def flag_is_true(v: Any) -> bool:
    return str(v).strip().lower() in {"1", "true", "sim", "yes", "y", "s"}


def logout():
    keys_to_clear = [
        "auth",
        "item",
        "login_user",
        "login_pass",
        "confirm_delete",
        "creating_new",
        "gspread_client",
        "spreadsheet",
    ]

    for k in keys_to_clear:
        st.session_state.pop(k, None)

    clear_sheet_caches()


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
        return flag_is_true(auth.get("can_drinks"))

    return flag_is_true(auth.get("can_pratos"))


def validate_users_df(users: pd.DataFrame):
    missing = [c for c in REQUIRED_USER_COLS if c not in users.columns]

    if missing:
        raise ValueError(f"Faltam colunas na aba users: {', '.join(missing)}")


def ensure_item_min_schema(items: pd.DataFrame) -> pd.DataFrame:
    out = items.copy()

    for c in BASE_ITEM_COLS:
        if c not in out.columns:
            out[c] = ""

    return out.fillna("")


def next_id(items: pd.DataFrame, prefix: str) -> str:
    if items.empty or "id" not in items.columns:
        return f"{prefix}001"

    ids = items["id"].astype(str).tolist()
    nums: List[int] = []

    for x in ids:
        x = str(x).strip()
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
    pref = [c for c in all_cols if c.startswith(prefix)]

    priority = [
        f"{prefix}ingredients",
        f"{prefix}steps",
        f"{prefix}plating",
        f"{prefix}mise_en_place",
        f"{prefix}details",
        f"{prefix}common_mistakes",
        f"{prefix}quality_check",
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


def safe_image(url_or_path: str) -> bool:
    try:
        st.image(url_or_path, use_container_width=True)
        return True
    except Exception:
        return False


def safe_video(url: str) -> bool:
    try:
        st.video(url)
        return True
    except Exception:
        return False


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


def render_media(item: Dict[str, str], all_cols: List[str]):
    if "cover_photo_url" in all_cols:
        raw = str(item.get("cover_photo_url", "")).strip()

        if raw:
            thumb = drive_thumbnail_url(raw)
            direct = normalize_drive_direct_view(raw)
            shown = False

            if thumb:
                shown = safe_image(thumb)

            if not shown:
                shown = safe_image(direct)

            if not shown:
                safe_image(raw)

    if "training_video_url" in all_cols:
        rawv = str(item.get("training_video_url", "")).strip()

        if rawv:
            yt_id = extract_youtube_id(rawv)

            if yt_id:
                safe_video(normalize_youtube_url(rawv))
            else:
                preview = drive_preview_url(rawv)

                if preview:
                    st.markdown(
                        f'<iframe src="{preview}" width="100%" height="420" '
                        f'style="border:none;border-radius:12px;"></iframe>',
                        unsafe_allow_html=True,
                    )
                else:
                    if not safe_video(rawv):
                        st.caption("Vídeo indisponível no momento.")


def build_item_from_row(row: pd.Series, all_cols: List[str]) -> Dict[str, str]:
    item = {}

    for c in all_cols:
        item[c] = str(row.get(c, ""))

    return item


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
            try:
                st.image(lp, use_container_width=True)
            except Exception:
                pass

    if auth:
        _, _, col3 = st.columns([2, 2, 2])

        with col3:
            st.markdown('<div class="small-btn">', unsafe_allow_html=True)
            if st.button("Trocar usuário", use_container_width=True, key="btn_trocar_usuario"):
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

        for c in ["active", "can_drinks", "can_pratos"]:
            if c in df.columns:
                df[c] = df[c].astype(str).str.strip()
            else:
                df[c] = ""

        df["username"] = df["username"].astype(str).str.strip()
        df["password"] = df["password"].astype(str).str.strip()

        match = df[
            (df["username"] == str(u).strip())
            & (df["password"] == str(p).strip())
            & (df["active"] == "1")
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


def admin_item_form(
    item: Dict[str, str],
    all_cols: List[str],
    tipo_val: str,
    items_tab: str,
    creating_new: bool,
):
    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.subheader("Administrador · Gerenciar item")

    with st.form("admin_item_form"):
        edited = dict(item)

        col1, col2 = st.columns([1, 1])

        with col1:
            current_type = str(item.get("type", tipo_val)).strip()
            edited["type"] = st.selectbox(
                "Tipo",
                ["drink", "prato"],
                index=0 if current_type == "drink" else 1,
            )

            if "category" in all_cols:
                edited["category"] = st.text_input(
                    "Categoria",
                    value=str(item.get("category", "")),
                )

            if "yield" in all_cols:
                edited["yield"] = st.text_input(
                    "Rendimento",
                    value=str(item.get("yield", "")),
                )

        with col2:
            st.text_input("ID", value=str(item.get("id", "")), disabled=True)
            edited["id"] = str(item.get("id", ""))

            edited["name"] = st.text_input(
                "Título (nome)",
                value=str(item.get("name", "")),
            )

            if "total_time_min" in all_cols:
                edited["total_time_min"] = st.text_input(
                    "Tempo total (min)",
                    value=str(item.get("total_time_min", "")),
                )

        if "tags" in all_cols:
            edited["tags"] = st.text_input(
                "Tags (separadas por vírgula)",
                value=str(item.get("tags", "")),
            )

        if "concept" in all_cols:
            edited["concept"] = st.text_area(
                "Concept",
                value=str(item.get("concept", "")),
                height=100,
            )

        if "strategy" in all_cols:
            edited["strategy"] = st.text_area(
                "Strategy",
                value=str(item.get("strategy", "")),
                height=100,
            )

        if "cover_photo_url" in all_cols:
            edited["cover_photo_url"] = st.text_input(
                "Foto capa (URL ou Drive)",
                value=str(item.get("cover_photo_url", "")),
            )

        if "training_video_url" in all_cols:
            edited["training_video_url"] = st.text_input(
                "Vídeo treinamento (URL ou Drive)",
                value=str(item.get("training_video_url", "")),
            )

        st.markdown("<hr/>", unsafe_allow_html=True)

        service_cols = get_mode_cols(all_cols, "service_")

        with st.expander("Campos de Serviço (service_*)", expanded=True):
            if not service_cols:
                st.info("Nenhuma coluna service_* encontrada na planilha.")

            for c in service_cols:
                edited[c] = st.text_area(
                    prettify_label(c),
                    value=str(item.get(c, "")),
                    height=120,
                )

        training_cols = get_mode_cols(all_cols, "training_")

        with st.expander("Campos de Treinamento (training_*)", expanded=True):
            if not training_cols:
                st.info("Nenhuma coluna training_* encontrada na planilha.")

            for c in training_cols:
                edited[c] = st.text_area(
                    prettify_label(c),
                    value=str(item.get(c, "")),
                    height=120,
                )

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
            st.session_state["item"] = edited.get("id", "")

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
            edited["concept"] = st.text_area(
                "Concept",
                value=str(item.get("concept", "")),
                height=100,
            )

        if "strategy" in all_cols:
            edited["strategy"] = st.text_area(
                "Strategy",
                value=str(item.get("strategy", "")),
                height=100,
            )

        if "cover_photo_url" in all_cols:
            edited["cover_photo_url"] = st.text_input(
                "Foto capa (URL ou Drive)",
                value=str(item.get("cover_photo_url", "")),
            )

        if "training_video_url" in all_cols:
            edited["training_video_url"] = st.text_input(
                "Vídeo treinamento (URL ou Drive)",
                value=str(item.get("training_video_url", "")),
            )

        st.markdown("<hr/>", unsafe_allow_html=True)

        service_cols = get_mode_cols(all_cols, "service_")

        with st.expander("Campos de Serviço (service_*)", expanded=True):
            if not service_cols:
                st.info("Nenhuma coluna service_* encontrada na planilha.")

            for c in service_cols:
                edited[c] = st.text_area(
                    prettify_label(c),
                    value=str(item.get(c, "")),
                    height=120,
                )

        training_cols = get_mode_cols(all_cols, "training_")

        with st.expander("Campos de Treinamento (training_*)", expanded=True):
            if not training_cols:
                st.info("Nenhuma coluna training_* encontrada na planilha.")

            for c in training_cols:
                edited[c] = st.text_area(
                    prettify_label(c),
                    value=str(item.get(c, "")),
                    height=120,
                )

        save_clicked = st.form_submit_button("Salvar", type="primary", use_container_width=True)

    if save_clicked:
        try:
            update_item_row(items_tab, edited)

            st.toast("Salvo com sucesso.")
            st.rerun()

        except Exception as e:
            st.error(f"Falha ao salvar: {e}")

    st.markdown("</div>", unsafe_allow_html=True)


def render_item_view(item: Dict[str, str], all_cols: List[str], items_tab: str):
    st.markdown("<div class='card'>", unsafe_allow_html=True)

    title = str(item.get("name", "")).strip() or "Item sem nome"
    category = str(item.get("category", "")).strip()
    item_type = str(item.get("type", "")).strip()

    st.subheader(title)

    caption_parts = []
    if category:
        caption_parts.append(category)
    if item_type:
        caption_parts.append("Drink" if item_type == "drink" else "Prato")

    if caption_parts:
        st.caption(" · ".join(caption_parts))

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

        if extra_cols:
            visible_extras = [
                c for c in extra_cols
                if str(item.get(c, "")).strip()
            ]

            if visible_extras:
                with st.expander("Outras informações", expanded=False):
                    for c in visible_extras:
                        st.markdown(f"**{prettify_label(c)}**")
                        st.write(str(item.get(c, "")).strip())

    with right:
        render_media(item, all_cols)

    st.markdown("</div>", unsafe_allow_html=True)

    mode = st.radio(
        "Modo de uso",
        ["Serviço", "Treinamento"],
        horizontal=True,
        key=f"mode_{item.get('id', '')}",
    )

    st.markdown("<div class='card'>", unsafe_allow_html=True)

    if mode == "Serviço":
        service_cols = get_mode_cols(all_cols, "service_")
        render_text_sections(item, service_cols)
    else:
        training_cols = get_mode_cols(all_cols, "training_")
        render_text_sections(item, training_cols)

    st.markdown("</div>", unsafe_allow_html=True)

    if can_edit():
        if is_admin():
            admin_item_form(
                item=item,
                all_cols=all_cols,
                tipo_val=str(item.get("type", "")),
                items_tab=items_tab,
                creating_new=False,
            )
        else:
            editor_item_form(
                item=item,
                all_cols=all_cols,
                items_tab=items_tab,
            )


def select_item_screen(items: pd.DataFrame, items_tab: str):
    items = ensure_item_min_schema(items)

    if items.empty:
        st.warning("Nenhum item encontrado na aba de itens.")

        if is_admin():
            render_create_new_item(items, items_tab)

        return

    items["id"] = items["id"].astype(str).str.strip()
    items["type"] = items["type"].astype(str).str.strip()
    items["name"] = items["name"].astype(str).str.strip()

    available = items[
        items["type"].apply(lambda x: has_access(str(x).strip()))
    ].copy()

    if available.empty:
        st.warning("Seu usuário não possui acesso a pratos ou drinks cadastrados.")
        return

    tipo_opcoes = []

    if any(available["type"] == "prato"):
        tipo_opcoes.append("Pratos")

    if any(available["type"] == "drink"):
        tipo_opcoes.append("Drinks")

    if not tipo_opcoes:
        tipo_opcoes = ["Todos"]

    st.markdown("<div class='card'>", unsafe_allow_html=True)

    col1, col2 = st.columns([1, 2])

    with col1:
        filtro_tipo = st.selectbox(
            "Categoria",
            tipo_opcoes,
            key="filtro_tipo",
        )

    with col2:
        busca = st.text_input(
            "Buscar ficha",
            placeholder="Digite parte do nome, categoria ou tag",
            key="busca_item",
        )

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

        if is_admin():
            render_create_new_item(items, items_tab)

        return

    labels = []
    id_by_label = {}

    for _, row in filtered.iterrows():
        name = str(row.get("name", "")).strip() or "Sem nome"
        item_id = str(row.get("id", "")).strip()
        category = str(row.get("category", "")).strip()
        tipo = "Drink" if str(row.get("type", "")).strip() == "drink" else "Prato"

        label_parts = [name]
        extra = " · ".join([x for x in [tipo, category, item_id] if x])
        if extra:
            label_parts.append(f"({extra})")

        label = " ".join(label_parts)
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

    row = selected.iloc[0]
    all_cols = list(items.columns)
    item = build_item_from_row(row, all_cols)

    render_item_view(item, all_cols, items_tab)

    if is_admin():
        render_create_new_item(items, items_tab)


def render_create_new_item(items: pd.DataFrame, items_tab: str):
    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.subheader("Administrador · Criar nova ficha")

    col1, col2 = st.columns([1, 2])

    with col1:
        tipo_novo = st.selectbox(
            "Tipo da nova ficha",
            ["prato", "drink"],
            format_func=lambda x: "Prato" if x == "prato" else "Drink",
            key="novo_tipo",
        )

    with col2:
        criar = st.button("Criar nova ficha", type="primary", use_container_width=True)

    st.markdown("</div>", unsafe_allow_html=True)

    if criar:
        try:
            items = ensure_item_min_schema(items)
            all_cols = list(items.columns)

            prefix = "P" if tipo_novo == "prato" else "D"
            new_id = next_id(items, prefix)

            new_item = {c: "" for c in all_cols}
            new_item["id"] = new_id
            new_item["type"] = tipo_novo
            new_item["name"] = "Nova ficha"

            update_item_row(items_tab, new_item)

            st.session_state["item"] = new_id
            st.session_state["creating_new"] = False

            st.toast("Nova ficha criada.")
            st.rerun()

        except Exception as e:
            st.error(f"Falha ao criar nova ficha: {e}")


def diagnostics_panel():
    with st.expander("Diagnóstico técnico", expanded=False):
        st.write("Use este painel apenas para checar configuração do app.")

        c1, c2, c3 = st.columns(3)

        with c1:
            if st.button("Testar dependências Google", use_container_width=True):
                try:
                    load_gspread()
                    st.success("Dependências Google carregadas com sucesso.")
                except Exception as e:
                    st.error(str(e))

        with c2:
            if st.button("Limpar cache local", use_container_width=True):
                clear_sheet_caches()
                st.session_state.pop("gspread_client", None)
                st.session_state.pop("spreadsheet", None)
                st.success("Cache local limpo.")

        with c3:
            if st.button("Recarregar app", use_container_width=True):
                st.rerun()

        st.caption(f"USERS_TAB: {get_users_tab()}")
        st.caption(f"ITEMS_TAB: {get_items_tab()}")

        try:
            st.caption(f"SHEET_ID: {get_sheet_id()}")
        except Exception as e:
            st.caption(f"SHEET_ID não carregado: {e}")


def main():
    header()

    config_errors = validate_runtime_config()

    if config_errors:
        st.error("Configuração incompleta do app.")
        for err in config_errors:
            st.write(f"- {err}")
        diagnostics_panel()
        st.stop()

    try:
        users_tab = get_users_tab()
        items_tab = get_items_tab()

        users = read_sheet_values_fast(users_tab)

        if "auth" not in st.session_state:
            login(users)
            diagnostics_panel()
            st.stop()

        items = read_sheet_values_fast(items_tab)
        select_item_screen(items, items_tab)

        diagnostics_panel()

    except Exception as e:
        st.error("Falha ao carregar o app.")
        st.exception(e)
        diagnostics_panel()
        st.stop()


if __name__ == "__main__":
    main()
