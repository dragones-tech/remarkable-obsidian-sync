import QtQuick
import Quickshell

// Placeholder. The picker arrives in phase D: tags and individual notebooks,
// plus the one-time pairing field.
Item {
  id: root

  property var shell: null
  property var manifest: null

  readonly property string home: Quickshell.env("HOME")
  implicitWidth: 0
  implicitHeight: 0
}
