# Streamlit Helpers

"""
Utilitaires pour Streamlit.
"""

import uuid
from datetime import datetime
from typing import Dict, Any, Optional


def get_streamlit_config() -> Dict[str, Any]:
    """
    Récupérer la configuration Streamlit.

    Returns:
        Configuration de Streamlit
    """
    return {
        "page_title": "PawFlow - Pipeline Framework",
        "page_icon": "🚀",
        "layout": "wide",
        "initial_sidebar_state": "expanded",
    }


def format_bytes(size: int) -> str:
    """
    Formater une taille en octets en string lisible.

    Args:
        size: Taille en octets

    Returns:
        Taille formatée (ex: 1.5 MB)
    """
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} PB"


def format_duration(milliseconds: float) -> str:
    """
    Formater une durée en millisecondes en string lisible.

    Args:
        milliseconds: Durée en millisecondes

    Returns:
        Durée formatée (ex: 1m 30s)
    """
    seconds = milliseconds / 1000

    if seconds < 1:
        return f"{milliseconds:.0f} ms"
    elif seconds < 60:
        return f"{seconds:.2f} s"
    elif seconds < 3600:
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes}m {secs}s"
    else:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        return f"{hours}h {minutes}m"


def generate_unique_id(prefix: str = "") -> str:
    """
    Générer un ID unique.

    Args:
        prefix: Préfixe optionnel

    Returns:
        ID unique
    """
    return f"{prefix}{uuid.uuid4().hex[:8]}"


def get_timestamp() -> str:
    """
    Récupérer l'horodatage actuel.

    Returns:
        Horodatage au format ISO
    """
    return datetime.now().isoformat()


def parse_timestamp(timestamp_str: str) -> Optional[datetime]:
    """
    Parser une chaîne horodatée.

    Args:
        timestamp_str: Chaîne horodatée

    Returns:
        Objet datetime ou None
    """
    try:
        return datetime.fromisoformat(timestamp_str)
    except (ValueError, TypeError):
        return None


def truncate_string(text: str, max_length: int = 50, suffix: str = "...") -> str:
    """
    Tronquer une chaîne si trop longue.

    Args:
        text: Chaîne à tronquer
        max_length: Longueur maximale
        suffix: Suffixe à ajouter

    Returns:
        Chaîne tronquée ou originale
    """
    if len(text) <= max_length:
        return text
    return text[: max_length - len(suffix)] + suffix


def get_session_state(key: str, default: Any = None) -> Any:
    """
    Récupérer une valeur du session_state de manière sécurisée.

    Args:
        key: Clé du session_state
        default: Valeur par défaut

    Returns:
        Valeur du session_state ou par défaut
    """
    import streamlit as st

    return st.session_state.get(key, default)


def set_session_state(key: str, value: Any):
    """
    Définir une valeur dans le session_state.

    Args:
        key: Clé du session_state
        value: Valeur à définir
    """
    import streamlit as st

    st.session_state[key] = value


def clear_session_state(keys: Optional[list] = None):
    """
    Effacer des clés du session_state.

    Args:
        keys: Liste de clés à effacer (None pour tout effacer)
    """
    import streamlit as st

    if keys is None:
        st.session_state.clear()
    else:
        for key in keys:
            if key in st.session_state:
                del st.session_state[key]