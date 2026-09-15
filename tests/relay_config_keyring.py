"""Empty credential boundary for the disposable Electron configuration probe.

No credentials are required by this probe. Reads return no credential and any
attempt to write/delete one fails; production configuration and CLI are unmodified.
"""

from keyring.backend import KeyringBackend


class EmptyKeyring(KeyringBackend):
    priority = 1

    def get_password(self, service, username):
        return None

    def set_password(self, service, username, password):
        raise AssertionError("The configuration probe must not write credentials")

    def delete_password(self, service, username):
        raise AssertionError("The configuration probe must not delete credentials")
