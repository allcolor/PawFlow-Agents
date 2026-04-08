# Storage Module

"""
Module de stockage.
Fournit différents backends pour le stockage des données.
"""

from config.storage.filesystem_storage import FilesystemStorage
from config.storage.sqlite_storage import SqliteStorage
from config.storage.git_storage import GitStorage
from config.storage.postgres_storage import PostgresStorage

__all__ = ["FilesystemStorage", "SqliteStorage", "GitStorage", "PostgresStorage"]