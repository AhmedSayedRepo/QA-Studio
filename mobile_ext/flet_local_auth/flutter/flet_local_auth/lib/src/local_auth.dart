import 'dart:async';

import 'package:flet/flet.dart';
import 'package:flutter/material.dart';
import 'package:local_auth/local_auth.dart';

/// Dart side of the flet_local_auth extension. Mirrors flet_secure_storage's
/// SecureStorageService (same FletService + addInvokeMethodListener pattern),
/// but wraps `local_auth` instead of `flutter_secure_storage`.
///
/// NOTE: the host Activity MUST extend FlutterFragmentActivity for the
/// BiometricPrompt to attach — the build patches MainActivity for exactly this
/// (see build-apk.yml). Without it, authenticate() throws and resolves False.
class LocalAuthService extends FletService {
  LocalAuthService({required super.control});

  final LocalAuthentication _auth = LocalAuthentication();

  @override
  void init() {
    super.init();
    debugPrint("LocalAuthService(${control.id}).init: ${control.properties}");
    control.addInvokeMethodListener(_invokeMethod);
  }

  Future<dynamic> _invokeMethod(String name, dynamic args) async {
    switch (name) {
      case "authenticate":
        // Never let an exception cross the bridge — a cancelled prompt, missing
        // enrollment, or lockout all mean "not authenticated", which callers
        // treat uniformly as False (the gate then falls through to
        // email+password rather than getting stuck).
        try {
          return await _auth.authenticate(
            localizedReason: (args?["reason"] ?? "Authenticate to continue")
                as String,
            options: AuthenticationOptions(
              biometricOnly: (args?["biometric_only"] ?? false) as bool,
              stickyAuth: true,
              useErrorDialogs: true,
            ),
          );
        } catch (e) {
          debugPrint("LocalAuthService.authenticate error: $e");
          return false;
        }
      case "is_device_supported":
        try {
          return await _auth.isDeviceSupported();
        } catch (e) {
          debugPrint("LocalAuthService.is_device_supported error: $e");
          return false;
        }
      case "can_check_biometrics":
        try {
          return await _auth.canCheckBiometrics;
        } catch (e) {
          debugPrint("LocalAuthService.can_check_biometrics error: $e");
          return false;
        }
      default:
        throw Exception("Unknown LocalAuth method: $name");
    }
  }

  @override
  void dispose() {
    debugPrint("LocalAuthService(${control.id}).dispose()");
    control.removeInvokeMethodListener(_invokeMethod);
    super.dispose();
  }
}
