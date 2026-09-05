import QtQuick
import Quickshell
import qs.Commons
import qs.Ui

// Placeholder. The bar icon proper arrives in phase C: it exists only while a
// reMarkable is plugged in, and owns a popout listing what the tablet holds
// that the vault does not.
Panel {
  id: root
  moduleName: "io.github.dragones-tech.remarkable-sync"

  readonly property string home: Quickshell.env("HOME")
  readonly property string binDir: home + "/.config/omarchy/plugins/" + moduleName + "/bin"

  implicitWidth: 0
  implicitHeight: 0
  visible: false
}
