# Power BI report

The `Medtech Risk Intelligence` folder contains the Power BI Project (`.pbip`), report definition, and semantic model source.
Open `Medtech Risk Intelligence/Medtech Risk Intelligence.pbip` in Power BI Desktop.

## Configure the export workbook

The semantic model uses the `DataFile` parameter in
`Medtech Risk Intelligence/Medtech Risk Intelligence.SemanticModel/definition/expressions.tmdl` to locate the Excel export.
The checked-in parameter value points to the original local path (`C:\PowerBI\MedTech\PowerBI_Export.xlsx`); it will likely need to be changed on another computer.

To refresh the report on your computer:

1. Run the Python pipeline to create `data/gold/PowerBI_Export.xlsx`.
2. In Power BI Desktop, update the `DataFile` parameter to the full path of that generated workbook.
3. Refresh the semantic model.

The generated workbook and Power BI local state/cache are intentionally not included in this repository.
