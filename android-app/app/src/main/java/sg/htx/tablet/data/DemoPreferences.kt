package sg.htx.tablet.data

import android.content.SharedPreferences
import sg.htx.tablet.viewmodel.DemoSettingsStore
import sg.htx.tablet.viewmodel.Settings

/** Credentials remain encrypted by TokenStore; no plaintext token preference. */
class DemoPreferences(private val prefs: SharedPreferences, private val tokens: BackendTokenStore = TokenStore(prefs)) : DemoSettingsStore {
    val clientId: String = prefs.getString("demo_client_id", null) ?: java.util.UUID.randomUUID().toString().also {
        prefs.edit().putString("demo_client_id", it).apply()
    }
    override fun load() = Settings(prefs.getString("base", null) ?: "http://10.0.2.2:8100",
        prefs.getString("session", "") ?: "", tokens.read(), prefs.getBoolean("configured", prefs.contains("base")),
        prefs.getString("video", "") ?: "", runCatching { sg.htx.tablet.data.InputSource.valueOf(prefs.getString("source", "RECORDED_VIDEO")!!) }.getOrDefault(InputSource.RECORDED_VIDEO))
    override fun save(settings: Settings) {
        tokens.save(settings.token)
        prefs.edit().putString("base", settings.base).putString("session", settings.session)
            .putString("video", settings.video).putString("source", settings.source.name).putBoolean("configured", settings.configured).apply()
    }
}
