package sg.htx.tablet

import android.content.SharedPreferences
import java.lang.reflect.Proxy
import org.junit.Assert.*
import org.junit.Test
import sg.htx.tablet.data.*
import sg.htx.tablet.viewmodel.Settings

class DemoPreferencesTest {
    @Test fun configurationRoundTripsAndTokenUsesSecureStoreOnly() {
        val values = mutableMapOf<String, Any?>()
        val editor = Proxy.newProxyInstance(SharedPreferences.Editor::class.java.classLoader,
            arrayOf(SharedPreferences.Editor::class.java)) { self, method, args ->
            when (method.name) {
                "putString", "putBoolean" -> { values[args!![0] as String] = args[1]; self }
                "apply" -> null
                else -> error(method.name)
            }
        } as SharedPreferences.Editor
        val prefs = Proxy.newProxyInstance(SharedPreferences::class.java.classLoader,
            arrayOf(SharedPreferences::class.java)) { _, method, args ->
            when (method.name) {
                "getString", "getBoolean" -> values[args!![0]] ?: args[1]
                "contains" -> values.containsKey(args!![0])
                "edit" -> editor
                else -> error(method.name)
            }
        } as SharedPreferences
        val secureStore = object : BackendTokenStore {
            var value = ""
            override fun read() = value
            override fun save(token: String) { value = token }
        }
        assertFalse(DemoPreferences(prefs, secureStore).load().configured)
        val firstClientId = DemoPreferences(prefs, secureStore).clientId
        assertEquals(firstClientId, DemoPreferences(prefs, secureStore).clientId)
        assertEquals(36, firstClientId.length)
        val expected = Settings("https://backend", "internal-id", "private-token", true, "one.MOV", InputSource.TABLET_CAMERA)
        DemoPreferences(prefs, secureStore).save(expected)
        assertEquals(expected, DemoPreferences(prefs, secureStore).load())
        assertEquals("private-token", secureStore.value)
        assertFalse(values.values.contains("private-token"))
        DemoPreferences(prefs, secureStore).save(expected.copy(token = ""))
        assertEquals("", DemoPreferences(prefs, secureStore).load().token)
    }
}
