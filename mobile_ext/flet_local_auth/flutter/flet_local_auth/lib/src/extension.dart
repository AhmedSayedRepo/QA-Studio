import 'package:flet/flet.dart';

import 'local_auth.dart';

class Extension extends FletExtension {
  @override
  FletService? createService(Control control) {
    switch (control.type) {
      case "LocalAuth":
        return LocalAuthService(control: control);
      default:
        return null;
    }
  }
}
