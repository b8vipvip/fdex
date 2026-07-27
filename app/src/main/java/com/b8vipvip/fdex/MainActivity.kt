package com.b8vipvip.fdex

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import com.b8vipvip.fdex.ui.FdexApp
import com.b8vipvip.fdex.ui.theme.FdexTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            FdexTheme {
                FdexApp()
            }
        }
    }
}
