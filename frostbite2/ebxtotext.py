import os
from struct import unpack,pack
import ebx
import sys

#Choose where you dumped the files and where to put the resulting TXT files.
dumpDirectory   = r"E:\hexing\bf3_dump"
targetDirectory = r"D:\hexing\test_ebx"
inputFolder     = r"" #relative to ebxFolder

ebxFolder       = r"bundles\ebx" #relative to the dumpDirectory

jsonFormat = True

##############################################################
##############################################################

fileExt = ".txt"
if jsonFormat:
    fileExt = ".json"

ebxFolder   = os.path.join(dumpDirectory,ebxFolder)
inputFolder = os.path.join(ebxFolder,inputFolder)

if len(sys.argv)>1:
    for fname in sys.argv:
        if fname[-4:]!=".ebx" or not os.path.isfile(fname):
            continue

        dbx=ebx.Dbx(fname,"", jsonFormat)
        outName=fname[:-4]+fileExt
        dbx.dump(outName)
else:
    print("Loading GUID table...")
    ebx.loadGuidTable(dumpDirectory)

    for dir0, dirs, ff in os.walk(inputFolder):
        for fname in ff:
            dbx=ebx.Dbx(os.path.join(dir0,fname),ebxFolder, jsonFormat)
            outName=os.path.join(targetDirectory,dbx.trueFilename+fileExt)
            dbx.dump(outName)
