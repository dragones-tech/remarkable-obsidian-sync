import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// A bar icon that exists only while a reMarkable is plugged in, and the popout
// it owns: which tagged notebooks the tablet holds that the vault does not,
// and the one key that closes that gap.
//
// The icon is the whole point. A tablet is connected for a minute at a time,
// so a widget that sat there permanently would spend its life saying "no
// tablet" - and the moment it matters is exactly the moment the icon appears.
Panel {
  id: root
  moduleName: "io.github.dragones-tech.remarkable-sync"

  readonly property string home: Quickshell.env("HOME")
  readonly property string omarchyPath: Quickshell.env("OMARCHY_PATH")
  readonly property string binDir: home + "/.config/omarchy/plugins/" + moduleName + "/bin"

  property var config: ({
    pollSeconds: 4,
    autoSync: false
  })

  // What the cheap probe last saw. `connected` drives the icon's existence.
  property bool connected: false
  property string iface: ""

  // Key authentication is a prerequisite, not a nicety: a sync triggered by
  // plugging in has no terminal and can never answer a password prompt.
  property bool paired: false
  property bool pairKnown: false

  // What the last report said about the gap between tablet and vault.
  property int pendingCount: 0
  property int selectedCount: 0
  property int trackedCount: 0
  property var notebooks: []
  property string statusError: ""
  property bool counting: false
  property bool syncing: false
  property string lastResult: ""

  property int selectedIndex: 0
  property bool cursorActive: false

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  // Notebooks with something new: the panel answers "what would syncing bring
  // me" before anything else.
  readonly property var pendingRows: {
    var out = []
    for (var i = 0; i < root.notebooks.length; i++) {
      var status = String(root.notebooks[i].status || "")
      if (status === "new" || status === "changed") out.push(root.notebooks[i])
    }
    return out
  }

  // With nothing pending, "all synced" is true but says nothing you can act
  // on. What you already sync is what you actually came to look at.
  readonly property bool showingPending: root.pendingRows.length > 0
  readonly property var rows: root.showingPending ? root.pendingRows : root.notebooks
  readonly property int rowCount: root.rows.length

  visible: root.connected
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  // ------------------------------------------------------------------------
  // Actions
  // ------------------------------------------------------------------------

  function applyConfig(raw) {
    var parsed = {}
    try { parsed = JSON.parse(raw || "{}") } catch (e) { parsed = {} }
    root.config = {
      pollSeconds: Number(parsed.pollSeconds || 4),
      autoSync: parsed.autoSync === true
    }
  }

  function probe() {
    if (probeProcess.running) return
    probeProcess.command = [root.binDir + "/rmos-probe"]
    probeProcess.running = true
  }

  function checkPairing() {
    if (pairProcess.running) return
    pairProcess.command = [root.binDir + "/rmos-pair", "--check"]
    pairProcess.running = true
  }

  // Costs an SSH round trip per selected notebook, so it runs when the tablet
  // appears or the panel is opened - never on the poll timer.
  function refresh() {
    if (reportProcess.running || !root.connected) return
    root.counting = true
    reportProcess.command = [root.binDir + "/rmos-report"]
    reportProcess.running = true
  }

  function syncNow() {
    if (syncProcess.running || !root.connected) return
    root.syncing = true
    root.lastResult = ""
    syncProcess.command = [root.binDir + "/rmos-run"]
    syncProcess.running = true
  }

  function openVault() {
    root.close()
    Quickshell.execDetached([root.binDir + "/rmos-open"])
  }

  // The picker is a separate surface owned by the shell, so the widget asks
  // for it the same way a keybinding would rather than reaching across.
  function openPicker() {
    root.close()
    Quickshell.execDetached([
      root.omarchyPath + "/bin/omarchy-shell", "-q", "shell", "summon", root.moduleName,
      JSON.stringify({ view: root.paired ? "picker" : "pair" })
    ])
  }

  function activateCursor() {
    if (root.rowCount === 0) return
    root.openPicker()
  }

  function moveCursor(dx, dy) {
    if (root.rowCount === 0) return
    if (!root.cursorActive) {
      root.cursorActive = true
      root.selectedIndex = 0
      return
    }
    var next = root.selectedIndex + (dy !== 0 ? dy : dx)
    root.selectedIndex = Math.max(0, Math.min(root.rowCount - 1, next))
  }

  onOpenedChanged: if (opened) {
    configFile.reload()
    root.cursorActive = false
    root.selectedIndex = 0
    root.checkPairing()
    root.refresh()
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  // ------------------------------------------------------------------------
  // Inputs
  // ------------------------------------------------------------------------

  FileView {
    id: configFile
    path: root.home + "/.config/omarchy/remarkable-sync.json"
    watchChanges: true
    printErrors: false
    onLoaded: root.applyConfig(text())
    onFileChanged: reload()
    onLoadFailed: root.applyConfig("{}")
  }

  Timer {
    interval: Math.max(2, Number(root.config.pollSeconds || 4)) * 1000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: root.probe()
  }

  Process {
    id: probeProcess
    stdout: StdioCollector { id: probeOut; waitForEnd: true }
    onExited: function(exitCode) {
      var data = {}
      if (exitCode === 0) { try { data = JSON.parse(probeOut.text) } catch (e) { data = {} } }
      var wasConnected = root.connected
      root.connected = data.connected === true
      root.iface = String(data.interface || "")

      if (!root.connected) {
        root.notebooks = []
        root.pendingCount = 0
        root.statusError = ""
        return
      }
      // The tablet was already there when the shell started, so no transition
      // fired: look once, the first time we see it.
      if (!wasConnected) {
        root.checkPairing()
        root.refresh()
        if (root.config.autoSync === true) root.syncNow()
      }
    }
  }

  Process {
    id: pairProcess
    stdout: StdioCollector { id: pairOut; waitForEnd: true }
    onExited: function(exitCode) {
      var data = {}
      if (exitCode === 0) { try { data = JSON.parse(pairOut.text) } catch (e) { data = {} } }
      root.paired = data.paired === true
      root.pairKnown = true
    }
  }

  Process {
    id: reportProcess
    stdout: StdioCollector { id: reportOut; waitForEnd: true }
    onExited: function(exitCode) {
      root.counting = false
      var data = {}
      if (exitCode === 0) { try { data = JSON.parse(reportOut.text) } catch (e) { data = {} } }
      root.statusError = String(data.error || "")
      root.pendingCount = Number(data.pending || 0)
      root.selectedCount = Number(data.selected || 0)
      root.trackedCount = Number(data.tracked || 0)
      root.notebooks = data.notebooks || []
      if (root.selectedIndex >= root.rowCount) root.selectedIndex = Math.max(0, root.rowCount - 1)
    }
  }

  Process {
    id: syncProcess
    stdout: StdioCollector { id: syncOut; waitForEnd: true }
    onExited: function(exitCode) {
      root.syncing = false
      var data = {}
      if (exitCode === 0) { try { data = JSON.parse(syncOut.text) } catch (e) { data = {} } }
      if (data.error) {
        root.lastResult = String(data.error)
      } else {
        var updated = Number(data.updated || 0)
        var failed = Number(data.failed || 0)
        root.lastResult = failed > 0
          ? (updated + " synced, " + failed + " failed")
          : (updated === 0 ? "Nothing to sync" : (updated + " notebook" + (updated === 1 ? "" : "s") + " synced"))
      }
      Quickshell.execDetached(["notify-send", "-a", "reMarkable", "reMarkable", root.lastResult])
      root.refresh()
    }
  }

  // ------------------------------------------------------------------------
  // The icon
  // ------------------------------------------------------------------------

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    // fa-pencil. The bar should name what you just plugged in, not the file
    // format it happens to store. Written as an escape: a literal glyph in
    // source is one careless round trip away from becoming an empty string,
    // which renders as a hole rather than an error.
    text: "\uf040"
    // Pending notebooks are the only reason to act, so they are the only
    // thing that colours the icon.
    active: root.pendingCount > 0 || (root.pairKnown && !root.paired)
    tooltipText: {
      if (root.pairKnown && !root.paired) return "reMarkable · not paired"
      if (root.statusError !== "") return "reMarkable · " + root.statusError
      if (root.counting) return "reMarkable · checking"
      if (root.pendingCount > 0)
        return "reMarkable · " + root.pendingCount + " notebook" +
               (root.pendingCount === 1 ? "" : "s") + " to sync"
      return "reMarkable · all synced"
    }
    onPressed: function(buttonCode) {
      // Right-click syncs without opening anything, for the plug in, sync,
      // unplug loop that is most of the use.
      if (buttonCode === Qt.RightButton) root.syncNow()
      else root.toggle()
    }
  }

  // ------------------------------------------------------------------------
  // The popout
  // ------------------------------------------------------------------------

  KeyboardPanel {
    id: popout
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: popout.fittedContentWidth(Style.space(360))
    contentHeight: popout.fittedContentHeight(column.implicitHeight, Style.space(560))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onMoveRequested: function(dx, dy) { root.moveCursor(dx, dy) }
      onActivateRequested: if (root.cursorActive) root.activateCursor()
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(t) {
        var key = String(t).toLowerCase()
        if (key === "s") root.syncNow()
        else if (key === "o") root.openVault()
        else if (key === "t") root.openPicker()
        else if (key === "r") root.refresh()
      }

      Flickable {
        id: panelFlick
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
          id: column
          width: panelFlick.width
          spacing: Style.space(10)

          // --- header ---------------------------------------------------

          Item {
            id: header
            width: parent.width
            height: Math.max(brand.implicitHeight, headerActions.implicitHeight)

            Row {
              id: brand
              anchors.left: parent.left
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(8)

              Text {
                textFormat: Text.PlainText
                text: "\uf040"
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.icon
                anchors.verticalCenter: parent.verticalCenter
              }

              Column {
                anchors.verticalCenter: parent.verticalCenter
                spacing: Style.space(1)

                Text {
                  textFormat: Text.PlainText
                  text: "reMarkable"
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.subtitle
                }

                Text {
                  textFormat: Text.PlainText
                  visible: root.iface !== ""
                  text: root.iface
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                }
              }
            }

            Row {
              id: headerActions
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(6)

              HeaderButton {
                glyph: "\uf021"
                shortcut: "s"
                enabled: root.paired && !root.syncing
                onActivated: root.syncNow()
              }
              HeaderButton {
                glyph: "\uf02c"
                shortcut: "t"
                onActivated: root.openPicker()
              }
              HeaderButton {
                glyph: "\uf07c"
                shortcut: "o"
                onActivated: root.openVault()
              }
            }
          }

          PanelSeparator { width: parent.width }

          // --- what needs saying ----------------------------------------

          Text {
            width: parent.width
            visible: root.pairKnown && !root.paired
            wrapMode: Text.WordWrap
            textFormat: Text.PlainText
            text: "Not paired. A sync triggered by plugging in has no terminal, " +
                  "so it needs a key rather than a password. Press t to set that up once."
            color: root.urgent
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }

          Text {
            width: parent.width
            visible: root.statusError !== ""
            wrapMode: Text.WordWrap
            textFormat: Text.PlainText
            text: root.statusError
            color: root.urgent
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }

          Text {
            width: parent.width
            visible: root.statusError === "" && root.paired && root.counting
            textFormat: Text.PlainText
            text: "Checking what changed…"
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }

          Text {
            width: parent.width
            visible: root.statusError === "" && root.paired && !root.counting && root.selectedCount === 0
            wrapMode: Text.WordWrap
            textFormat: Text.PlainText
            text: "Nothing is marked for export yet. Tag a notebook on the tablet, " +
                  "or press t to pick what syncs."
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }

          Text {
            width: parent.width
            visible: root.statusError === "" && !root.counting && root.selectedCount > 0 && !root.showingPending
            textFormat: Text.PlainText
            text: "All synced."
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }

          PanelSectionHeader {
            width: parent.width
            visible: root.rowCount > 0
            text: root.showingPending ? "To sync" : "Syncing"
          }

          // --- the notebooks --------------------------------------------

          Column {
            width: parent.width
            spacing: Style.space(2)
            visible: root.rowCount > 0

            Repeater {
              model: root.rows
              delegate: NotebookRow {
                width: column.width
                notebook: modelData
                rowIndex: index
              }
            }
          }

          // --- footer ---------------------------------------------------

          Text {
            width: parent.width
            visible: root.lastResult !== ""
            textFormat: Text.PlainText
            text: root.lastResult
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }

          Text {
            width: parent.width
            textFormat: Text.PlainText
            text: "s sync · t choose · o open · r refresh"
            color: Qt.darker(root.dim, 1.2)
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }
        }
      }
    }
  }

  // ------------------------------------------------------------------------
  // Local components
  // ------------------------------------------------------------------------

  component HeaderButton: Rectangle {
    id: headerButton

    property string glyph: ""
    property string shortcut: ""
    property bool enabled: true
    property color tint: root.foreground

    signal activated()

    readonly property bool hot: headerButton.enabled && hover.containsMouse

    radius: Style.cornerRadius
    opacity: headerButton.enabled ? 1 : 0.4
    implicitWidth: headerContent.implicitWidth + Style.space(12)
    implicitHeight: headerContent.implicitHeight + Style.space(7)
    color: hot ? Qt.rgba(tint.r, tint.g, tint.b, 0.14) : "transparent"
    border.width: 1
    border.color: hot
      ? Qt.rgba(tint.r, tint.g, tint.b, 0.5)
      : Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.16)

    Behavior on color { ColorAnimation { duration: 90 } }

    Row {
      id: headerContent
      anchors.centerIn: parent
      spacing: Style.space(5)

      Text {
        textFormat: Text.PlainText
        text: headerButton.glyph
        color: headerButton.hot ? headerButton.tint : Qt.darker(headerButton.tint, 1.2)
        font.family: root.fontFamily
        font.pixelSize: Style.font.icon
        anchors.verticalCenter: parent.verticalCenter
      }

      Text {
        textFormat: Text.PlainText
        visible: headerButton.shortcut !== ""
        text: headerButton.shortcut
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        anchors.verticalCenter: parent.verticalCenter
      }
    }

    MouseArea {
      id: hover
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: headerButton.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
      onClicked: if (headerButton.enabled) headerButton.activated()
    }
  }

  component NotebookRow: CursorSurface {
    id: notebookRow

    property var notebook: null
    property int rowIndex: 0

    readonly property string title: notebook ? String(notebook.name || "") : ""
    readonly property string status: notebook ? String(notebook.status || "") : ""
    readonly property bool waiting: status === "new" || status === "changed"
    readonly property bool broken: status === "error"

    hasCursor: root.cursorActive && root.selectedIndex === rowIndex
    foreground: root.foreground
    implicitHeight: rowContent.implicitHeight + Style.spacing.rowPaddingX

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onEntered: { root.cursorActive = true; root.selectedIndex = notebookRow.rowIndex }
      onClicked: root.openPicker()
    }

    RowLayout {
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(10)
      anchors.rightMargin: Style.space(10)
      spacing: Style.space(8)

      Text {
        textFormat: Text.PlainText
        // A dot for something waiting, a check for what is already across.
        text: notebookRow.broken ? "\uf071" : (notebookRow.waiting ? "\uf111" : "\uf00c")
        color: notebookRow.broken ? root.urgent
             : (notebookRow.waiting ? root.foreground : root.dim)
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        Layout.alignment: Qt.AlignVCenter
      }

      ColumnLayout {
        id: rowContent
        Layout.fillWidth: true
        spacing: Style.space(1)

        Text {
          textFormat: Text.PlainText
          Layout.fillWidth: true
          elide: Text.ElideRight
          text: notebookRow.title
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
        }

        Text {
          textFormat: Text.PlainText
          Layout.fillWidth: true
          visible: notebookRow.broken
          elide: Text.ElideRight
          text: notebookRow.notebook ? String(notebookRow.notebook.error || "") : ""
          color: root.urgent
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
        }
      }

      Text {
        textFormat: Text.PlainText
        text: notebookRow.status
        color: notebookRow.broken ? root.urgent
             : (notebookRow.waiting ? root.foreground : root.dim)
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        Layout.alignment: Qt.AlignVCenter
      }
    }
  }
}
