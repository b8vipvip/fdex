package com.b8vipvip.fdex

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.b8vipvip.fdex.data.AppRepository
import com.b8vipvip.fdex.data.ensurePrivateAssistant
import com.b8vipvip.fdex.diagnostics.ClientRuntimeLog
import com.b8vipvip.fdex.ui.FdexApp
import com.b8vipvip.fdex.ui.theme.FdexTheme
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        ClientRuntimeLog.install(this)
        AppRepository(this).ensurePrivateAssistant()
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                ClientRuntimeLog.info("app", "foreground", "FDEX client entered foreground")
                while (isActive) {
                    ClientRuntimeLog.flush(this@MainActivity)
                    delay(30_000)
                }
            }
        }
        enableEdgeToEdge()
        setContent {
            FdexTheme {
                FdexApp()
            }
        }
    }
}
