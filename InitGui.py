"""Registers the Optical Table Designer workbench with FreeCAD."""
import FreeCADGui as Gui


class OpticalTableDesignerWorkbench(Gui.Workbench):
    MenuText = "Optical Table Designer"
    ToolTip = "Place optical CAD components on tables and breadboards"
    Icon = ""

    def Initialize(self):
        import optical_table_designer as otd
        Gui.addCommand("OTD_OpenPanel", otd.OpenPanelCommand())
        self.appendToolbar("Optical Table", ["OTD_OpenPanel"])
        self.appendMenu("Optical Table", ["OTD_OpenPanel"])

    def Activated(self):
        import optical_table_designer as otd
        otd.show_panel()


Gui.addWorkbench(OpticalTableDesignerWorkbench())
