// android-native-keyring-store's ndk-context bootstrap: the Rust store's JNI
// init symbol is statically linked into desktop_lib; calling it here (before
// TauriActivity spins up Rust) hands the JavaVM + application context to
// ndk-context so Keystore-backed credential storage works.
package io.crates.keyring

import android.content.Context

class Keyring {
    companion object {
        init {
            System.loadLibrary("desktop_lib")
        }
        external fun initializeNdkContext(context: Context)
    }
}
