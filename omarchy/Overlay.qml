import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import qs.Commons
import qs.Ui

// Choosing what syncs, and the one-time pairing that has to happen first.
//
// Two ways to mark a notebook, because the tablet offers two and neither is
// wrong: a tag, which marks every notebook carrying it and keeps working as
// you tag more, and a notebook picked by hand. They are unioned, so unticking
// a tag never un-picks a notebook you chose deliberately.
Item {
  id: root

  // Injected by shell.qml when it loads an overlay-kind plugin.
  property var shell: null
  property var manifest: null

  readonly property string home: Quickshell.env("HOME")
  readonly property string pluginId: manifest && manifest.id
    ? String(manifest.id) : "io.github.dragones-tech.remarkable-sync"
  readonly property string binDir: (manifest && manifest.__sourceDir
    ? String(manifest.__sourceDir)
    : home + "/.config/omarchy/plugins/io.github.dragones-tech.remarkable-sync") + "/bin"

  property bool opened: false
  // "pair" until a key is in place, "picker" afterwards. Pairing is not a
  // setting among others: nothing else here can work until it is done.
  property string view: "picker"

  property bool paired: false
  property bool pairing: false
  property bool loading: false
  property bool applying: false
  property string statusText: ""
  property bool statusIsError: false

  // What the tablet holds, as rmos-catalog last reported it.
  property var documents: []
  property var tagCounts: []

  // What is ticked now, and what was ticked when the catalogue arrived. The
  // difference is exactly what apply has to write.
  property var chosenTags: ({})
  property var originalTags: ({})
  property var chosenDocs: ({})
  property var originalDocs: ({})

  property string filterText: ""
  property int focusedSection: 0   // 0 tags, 1 notebooks

  readonly property color background: Color.menu.background
  readonly property color foreground: Color.menu.text
  readonly property color accent: Color.menu.selectedText
  readonly property color dim: Qt.darker(foreground, 1.5)
  readonly property color urgent: Color.urgent
  readonly property var borderSpec: Border.surfaceSpec("menu", "border", Color.menu.border, Math.max(1, Style.space(2)))
  readonly property color scrim: Color.menu.scrim
  readonly property int cornerRadius: Style.cornerRadius
  readonly property string fontFamily: Style.font.menuFamily
  readonly property int contentMargin: Style.spacing.panelPadding
  readonly property int contentSpacing: Style.spacing.md

  readonly property var visibleDocuments: {
    var needle = String(root.filterText).trim().toLowerCase()
    if (needle === "") return root.documents
    var out = []
    for (var i = 0; i < root.documents.length; i++) {
      var doc = root.documents[i]
      var hay = (String(doc.name || "") + " " + String(doc.folder || "") +
                 " " + (doc.tags || []).join(" ")).toLowerCase()
      if (hay.indexOf(needle) !== -1) out.push(doc)
    }
    return out
  }

  readonly property int chosenCount: {
    var total = 0
    for (var i = 0; i < root.documents.length; i++) {
      var doc = root.documents[i]
      if (root.chosenDocs[doc.uuid] === true) { total++; continue }
      var tags = doc.tags || []
      for (var t = 0; t < tags.length; t++) {
        if (root.chosenTags[String(tags[t]).toLowerCase()] === true) { total++; break }
      }
    }
    return total
  }

  readonly property bool dirty: {
    if (JSON.stringify(root.chosenTags) !== JSON.stringify(root.originalTags)) return true
    return JSON.stringify(root.chosenDocs) !== JSON.stringify(root.originalDocs)
  }

  // ------------------------------------------------------------ lifecycle

  function open(payloadJson) {
    var payload = ({})
    try { payload = JSON.parse(String(payloadJson || "{}")) || ({}) } catch (e) { payload = ({}) }

    root.view = String(payload.view || "picker")
    root.statusText = ""
    root.filterText = ""
    root.opened = true
    root.checkPairing()
    Qt.callLater(function() { keys.forceActiveFocus() })
  }

  function close() { root.opened = false }

  function dismiss() {
    root.close()
    if (root.shell && typeof root.shell.hide === "function") root.shell.hide(root.pluginId)
  }

  function toggle() {
    if (root.opened) root.dismiss()
    else root.open("{}")
  }

  function setStatus(text, isError) {
    root.statusText = String(text || "")
    root.statusIsError = isError === true
  }

  // ------------------------------------------------------------- pairing

  function checkPairing() {
    if (pairCheck.running) return
    pairCheck.command = [root.binDir + "/rmos-pair", "--check"]
    pairCheck.running = true
  }

  function pair() {
    if (root.pairing) return
    if (passwordField.text === "") { root.setStatus("Enter the tablet's password first", true); return }
    root.pairing = true
    root.setStatus("Pairing…", false)
    // The password goes down stdin, never in the argument list: argv is
    // visible to anything that can read /proc.
    pairProcess.secret = passwordField.text
    passwordField.text = ""
    pairProcess.command = [root.binDir + "/rmos-pair"]
    pairProcess.running = true
  }

  // ------------------------------------------------------------ catalogue

  function loadCatalogue() {
    if (catalogProcess.running) return
    root.loading = true
    catalogProcess.command = [root.binDir + "/rmos-catalog"]
    catalogProcess.running = true
  }

  function applyCatalogue(data) {
    root.documents = data.documents || []
    root.tagCounts = data.tags || []

    var tags = ({})
    var configured = (data.selection && data.selection.tags) || []
    for (var i = 0; i < configured.length; i++) tags[String(configured[i]).toLowerCase()] = true
    root.chosenTags = tags
    root.originalTags = JSON.parse(JSON.stringify(tags))

    var docs = ({})
    for (var d = 0; d < root.documents.length; d++) {
      var by = root.documents[d].selected_by || []
      if (by.indexOf("file") !== -1) docs[root.documents[d].uuid] = true
    }
    root.chosenDocs = docs
    root.originalDocs = JSON.parse(JSON.stringify(docs))
  }

  function toggleTag(name) {
    var key = String(name).toLowerCase()
    var next = JSON.parse(JSON.stringify(root.chosenTags))
    if (next[key] === true) delete next[key]
    else next[key] = true
    root.chosenTags = next
  }

  function toggleDoc(uuid) {
    var next = JSON.parse(JSON.stringify(root.chosenDocs))
    if (next[uuid] === true) delete next[uuid]
    else next[uuid] = true
    root.chosenDocs = next
  }

  // Only the difference is written, so a notebook that was already picked is
  // not re-selected and the tablet is not touched for no reason.
  function apply() {
    if (root.applying) return
    if (!root.dirty) { root.dismiss(); return }

    var args = [root.binDir + "/rmos-apply"]

    var tags = []
    for (var key in root.chosenTags) if (root.chosenTags[key] === true) tags.push(key)
    if (JSON.stringify(root.chosenTags) !== JSON.stringify(root.originalTags)) {
      args.push("--tags", JSON.stringify(tags))
    }

    for (var uuid in root.chosenDocs) {
      if (root.chosenDocs[uuid] === true && root.originalDocs[uuid] !== true) args.push("--select", uuid)
    }
    for (var was in root.originalDocs) {
      if (root.originalDocs[was] === true && root.chosenDocs[was] !== true) args.push("--unselect", was)
    }

    root.applying = true
    root.setStatus("Saving…", false)
    applyProcess.command = args
    applyProcess.running = true
  }

  // --------------------------------------------------------------- inputs

  Process {
    id: pairCheck
    stdout: StdioCollector { id: pairCheckOut; waitForEnd: true }
    onExited: function(exitCode) {
      var data = {}
      if (exitCode === 0) { try { data = JSON.parse(pairCheckOut.text) } catch (e) { data = {} } }
      root.paired = data.paired === true
      if (!root.paired) {
        root.view = "pair"
        Qt.callLater(function() { passwordField.forceActiveFocus() })
      } else if (root.view === "pair") {
        root.view = "picker"
      }
      if (root.paired && root.opened) root.loadCatalogue()
    }
  }

  Process {
    id: pairProcess
    // The password goes over stdin, never argv, and is dropped the moment it
    // has been handed over.
    property string secret: ""
    stdinEnabled: true
    stdout: StdioCollector { id: pairOut; waitForEnd: true }
    onStarted: {
      write(secret + "\n")
      secret = ""
    }
    onExited: function(exitCode) {
      root.pairing = false
      var data = {}
      if (exitCode === 0) { try { data = JSON.parse(pairOut.text) } catch (e) { data = {} } }
      if (data.error) {
        root.setStatus(String(data.error), true)
      } else {
        root.setStatus("Paired.", false)
        root.paired = true
        root.view = "picker"
        root.loadCatalogue()
        Qt.callLater(function() { keys.forceActiveFocus() })
      }
    }
  }

  Process {
    id: catalogProcess
    stdout: StdioCollector { id: catalogOut; waitForEnd: true }
    onExited: function(exitCode) {
      root.loading = false
      var data = {}
      if (exitCode === 0) { try { data = JSON.parse(catalogOut.text) } catch (e) { data = {} } }
      if (data.error) { root.setStatus(String(data.error), true); return }
      root.applyCatalogue(data)
      root.setStatus("", false)
    }
  }

  Process {
    id: applyProcess
    stdout: StdioCollector { id: applyOut; waitForEnd: true }
    onExited: function(exitCode) {
      root.applying = false
      var data = {}
      if (exitCode === 0) { try { data = JSON.parse(applyOut.text) } catch (e) { data = {} } }
      if (data.error || data.ok === false) {
        root.setStatus(String(data.error || "Some changes could not be saved"), true)
        return
      }
      Quickshell.execDetached(["notify-send", "-a", "reMarkable", "reMarkable",
                               root.chosenCount + " notebook(s) will sync"])
      root.dismiss()
    }
  }

  // --------------------------------------------------------------- the card

  PanelWindow {
    id: panel
    visible: root.opened
    anchors { top: true; bottom: true; left: true; right: true }
    color: "transparent"
    WlrLayershell.namespace: "omarchy-remarkable-sync"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.Exclusive
    exclusionMode: ExclusionMode.Ignore

    Rectangle { anchors.fill: parent; color: root.scrim }
    MouseArea { anchors.fill: parent; onClicked: root.dismiss() }

    BorderSurface {
      id: card
      width: Math.min(Style.space(720), panel.width - Style.gapsOut * 2)
      height: Math.min(Style.space(560), panel.height - Style.gapsOut * 2)
      radius: root.cornerRadius
      anchors.centerIn: parent
      color: root.background
      borderSpec: root.borderSpec
      padding: root.contentMargin

      MouseArea { anchors.fill: parent; onClicked: {} }

      Item {
        id: keys
        anchors.fill: parent
        focus: true

        Keys.onEscapePressed: root.dismiss()
        Keys.onPressed: function(event) {
          if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
            if (root.view === "pair") root.pair()
            else root.apply()
            event.accepted = true
          } else if (event.key === Qt.Key_Tab && root.view === "picker") {
            root.focusedSection = root.focusedSection === 0 ? 1 : 0
            event.accepted = true
          } else if (event.key === Qt.Key_Slash && root.view === "picker") {
            filterField.forceActiveFocus()
            event.accepted = true
          }
        }

        ColumnLayout {
          anchors.fill: parent
          anchors.topMargin: card.contentTopInset
          anchors.rightMargin: card.contentRightInset
          anchors.bottomMargin: card.contentBottomInset
          anchors.leftMargin: card.contentLeftInset
          spacing: root.contentSpacing

          // --- header -------------------------------------------------

          RowLayout {
            Layout.fillWidth: true
            spacing: Style.space(8)

            Text {
              textFormat: Text.PlainText
              text: "\uf040"
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.icon
            }

            ColumnLayout {
              Layout.fillWidth: true
              spacing: Style.space(1)

              Text {
                textFormat: Text.PlainText
                text: root.view === "pair" ? "Pair the tablet" : "What syncs to Obsidian"
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.subtitle
              }

              Text {
                textFormat: Text.PlainText
                visible: root.view === "picker"
                text: root.loading ? "Reading the tablet…"
                                   : root.chosenCount + " of " + root.documents.length + " notebooks"
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
              }
            }
          }

          PanelSeparator { Layout.fillWidth: true }

          // --- pairing ------------------------------------------------

          ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: root.view === "pair"
            spacing: root.contentSpacing

            Text {
              Layout.fillWidth: true
              wrapMode: Text.WordWrap
              textFormat: Text.PlainText
              text: "A sync triggered by plugging the tablet in has no terminal, so it " +
                    "cannot answer a password prompt. Enter the password once and it is " +
                    "used to install a key, then discarded — it is never written to disk.\n\n" +
                    "The password is on the tablet under Settings → Help → About."
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
            }

            TextField {
              id: passwordField
              Layout.fillWidth: true
              password: true
              placeholderText: "Tablet password"
              enabled: !root.pairing
              onAccepted: root.pair()
            }

            Item { Layout.fillHeight: true }
          }

          // --- picker: tags -------------------------------------------

          ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: root.view === "picker"
            spacing: root.contentSpacing

            PanelSectionHeader {
              Layout.fillWidth: true
              text: "Tags — every notebook carrying one of these syncs"
            }

            Flow {
              Layout.fillWidth: true
              spacing: Style.space(6)
              visible: root.tagCounts.length > 0

              Repeater {
                model: root.tagCounts
                delegate: Chip {
                  label: String(modelData.name)
                  detail: String(modelData.count)
                  checked: root.chosenTags[String(modelData.name).toLowerCase()] === true
                  onToggled: root.toggleTag(modelData.name)
                }
              }
            }

            Text {
              Layout.fillWidth: true
              visible: root.tagCounts.length === 0 && !root.loading
              wrapMode: Text.WordWrap
              textFormat: Text.PlainText
              text: "No notebook on the tablet carries a tag yet. Tag one there — on the " +
                    "notebook or any of its pages — and it will appear here."
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }

            // --- picker: notebooks ------------------------------------

            RowLayout {
              Layout.fillWidth: true
              spacing: Style.space(8)

              PanelSectionHeader {
                Layout.fillWidth: true
                text: "Notebooks — pick individual ones as well"
              }

              TextField {
                id: filterField
                Layout.preferredWidth: Style.space(200)
                placeholderText: "Filter  /"
                text: root.filterText
                onTextChanged: root.filterText = text
              }
            }

            Flickable {
              id: listFlick
              Layout.fillWidth: true
              Layout.fillHeight: true
              contentWidth: width
              contentHeight: docColumn.implicitHeight
              clip: true
              boundsBehavior: Flickable.StopAtBounds
              flickableDirection: Flickable.VerticalFlick
              ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

              Column {
                id: docColumn
                width: listFlick.width
                spacing: Style.space(1)

                Repeater {
                  model: root.visibleDocuments
                  delegate: DocumentRow {
                    width: docColumn.width
                    document: modelData
                  }
                }
              }
            }
          }

          // --- footer ---------------------------------------------------

          Text {
            Layout.fillWidth: true
            visible: root.statusText !== ""
            wrapMode: Text.WordWrap
            textFormat: Text.PlainText
            text: root.statusText
            color: root.statusIsError ? root.urgent : root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }

          Text {
            Layout.fillWidth: true
            textFormat: Text.PlainText
            text: root.view === "pair"
              ? "Enter pair · Esc cancel"
              : "Enter save · Tab section · / filter · Esc cancel"
            color: Qt.darker(root.dim, 1.2)
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }
        }
      }
    }
  }

  // ---------------------------------------------------------- components

  component Chip: Rectangle {
    id: chip
    property string label: ""
    property string detail: ""
    property bool checked: false
    signal toggled()

    readonly property bool hot: chipMouse.containsMouse

    radius: root.cornerRadius
    implicitWidth: chipRow.implicitWidth + Style.space(16)
    implicitHeight: chipRow.implicitHeight + Style.space(9)
    color: chip.checked
      ? Qt.rgba(root.accent.r, root.accent.g, root.accent.b, chip.hot ? 0.30 : 0.20)
      : (chip.hot ? Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.10) : "transparent")
    border.width: 1
    border.color: chip.checked
      ? Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.7)
      : Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.20)

    Behavior on color { ColorAnimation { duration: 90 } }

    Row {
      id: chipRow
      anchors.centerIn: parent
      spacing: Style.space(6)

      Text {
        textFormat: Text.PlainText
        text: chip.checked ? "\uf14a" : "\uf096"
        color: chip.checked ? root.accent : root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        anchors.verticalCenter: parent.verticalCenter
      }

      Text {
        textFormat: Text.PlainText
        text: chip.label
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
        anchors.verticalCenter: parent.verticalCenter
      }

      Text {
        textFormat: Text.PlainText
        visible: chip.detail !== ""
        text: chip.detail
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        anchors.verticalCenter: parent.verticalCenter
      }
    }

    MouseArea {
      id: chipMouse
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onClicked: chip.toggled()
    }
  }

  component DocumentRow: CursorSurface {
    id: docRow
    property var document: null

    readonly property string uuid: document ? String(document.uuid || "") : ""
    readonly property var tags: document ? (document.tags || []) : []
    // Ticked by hand, or already covered by a tag that is ticked. The second
    // kind is shown but cannot be unticked here: untick the tag instead.
    readonly property bool pickedByHand: root.chosenDocs[docRow.uuid] === true
    readonly property bool coveredByTag: {
      for (var i = 0; i < docRow.tags.length; i++) {
        if (root.chosenTags[String(docRow.tags[i]).toLowerCase()] === true) return true
      }
      return false
    }

    hasCursor: docMouse.containsMouse
    foreground: root.foreground
    implicitHeight: docContent.implicitHeight + Style.spacing.rowPaddingX

    MouseArea {
      id: docMouse
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: docRow.coveredByTag ? Qt.ArrowCursor : Qt.PointingHandCursor
      onClicked: if (!docRow.coveredByTag) root.toggleDoc(docRow.uuid)
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
        text: (docRow.pickedByHand || docRow.coveredByTag) ? "\uf14a" : "\uf096"
        color: docRow.coveredByTag ? root.accent
             : (docRow.pickedByHand ? root.foreground : Qt.darker(root.dim, 1.4))
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        Layout.alignment: Qt.AlignVCenter
      }

      ColumnLayout {
        id: docContent
        Layout.fillWidth: true
        spacing: Style.space(1)

        Text {
          textFormat: Text.PlainText
          Layout.fillWidth: true
          elide: Text.ElideRight
          text: docRow.document ? String(docRow.document.name || "") : ""
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
        }

        Text {
          textFormat: Text.PlainText
          Layout.fillWidth: true
          elide: Text.ElideRight
          visible: text !== ""
          text: {
            var parts = []
            var folder = docRow.document ? String(docRow.document.folder || "") : ""
            if (folder !== "") parts.push(folder)
            if (docRow.tags.length > 0) parts.push(docRow.tags.join(", "))
            return parts.join("  ·  ")
          }
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
        }
      }

      Text {
        textFormat: Text.PlainText
        visible: docRow.coveredByTag
        text: "by tag"
        color: root.accent
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        Layout.alignment: Qt.AlignVCenter
      }
    }
  }
}
