import 'dart:async';

import 'package:flet/flet.dart';
import 'package:flutter/material.dart';
import 'package:open_filex/open_filex.dart';

/// Dart side of flet_open_file. Mirrors flet_local_auth's service pattern.
///
/// Opening a downloaded .apk here makes Android resolve it to the package
/// installer directly, instead of the share-sheet chooser ft.Share produced.
/// Requires REQUEST_INSTALL_PACKAGES in the manifest (see build-apk.yml); the
/// system install confirmation and the one-time "unknown sources" grant still
/// apply and cannot be bypassed by any app.
class OpenFileService extends FletService {
  OpenFileService({required super.control});

  @override
  void init() {
    super.init();
    debugPrint("OpenFileService(${control.id}).init");
    control.addInvokeMethodListener(_invokeMethod);
  }

  Future<dynamic> _invokeMethod(String name, dynamic args) async {
    switch (name) {
      case "open":
        // Never let an exception cross the bridge — the caller falls back to
        // the share sheet on anything other than "done".
        try {
          final path = (args?["path"] ?? "") as String;
          if (path.isEmpty) return "error";
          final res = await OpenFilex.open(path);
          return res.type.name;
        } catch (e) {
          debugPrint("OpenFileService.open error: $e");
          return "error";
        }
      default:
        throw Exception("Unknown OpenFile method: $name");
    }
  }

  @override
  void dispose() {
    control.removeInvokeMethodListener(_invokeMethod);
    super.dispose();
  }
}
