from .settings import autostart_enabled, load_settings, save_settings, set_autostart


class SettingsService:
    def load(self):
        return load_settings()

    def save(self, settings):
        return save_settings(settings)

    def autostart_enabled(self):
        return autostart_enabled()

    def set_autostart(self, enabled, start_hidden=False):
        return set_autostart(enabled, start_hidden=start_hidden)
