#Ebx format is the cornerstone of Frostbite, it's an asset node of sorts used to reference actual game assets
#stored in chunk and res files as well as define scripts and configs for the game.
#Ebx is platform native endian.
import string
import sys
import os
import copy
from struct import unpack,pack
import shutil

def unpackLE(typ,data): return unpack("<"+typ,data)
def unpackBE(typ,data): return unpack(">"+typ,data)

def createGuidTableFast(inputFolder,ebxFolder):
    global guidTable
    guidTable=dict()

    for dir0, dirs, ff in os.walk(inputFolder):
        for fname in ff:
            path=os.path.join(dir0,fname)
            f=open(path,"rb")
            magic=f.read(4)
            if magic==b"\xCE\xD1\xB2\x0F":   bigEndian=False
            elif magic==b"\x0F\xB2\xD1\xCE": bigEndian=True
            else:
                f.close()
                continue
            #grab the file guid directly, absolute offset 48 bytes
            f.seek(48)
            fileguid=Guid(f.read(16),bigEndian)
            f.close()
            filename=os.path.relpath(path,ebxFolder)
            filename=os.path.splitext(filename)[0].replace("\\","/")
            guidTable[fileguid]=filename

def createGuidTable(inputFolder):
    global guidTable
    guidTable=dict()

    for dir0, dirs, ff in os.walk(inputFolder):
        for fname in ff:
            f=open(os.path.join(dir0,fname),"rb")
            dbx=Dbx(f,fname)
            guidTable[dbx.fileGUID]=dbx.trueFilename

def makeDirs(path):
    folderPath=os.path.dirname(path)
    if not os.path.isdir(folderPath): os.makedirs(folderPath)

def hasher(keyword): #32bit FNV-1 hash with FNV_offset_basis = 5381 and FNV_prime = 33
    hash = 5381
    for byte in keyword:
        hash = (hash*33) ^ ord(byte)
    return hash & 0xffffffff # use & because Python promotes the num instead of intended overflow
class Header:
    def __init__(self,varList): ##all 4byte unsigned integers
        self.absStringOffset     = varList[0]  ## absolute offset for string section start
        self.lenStringToEOF      = varList[1]  ## length from string section start to EOF
        self.numGUID             = varList[2]  ## number of external GUIDs
        self.null                = varList[3]  ## 00000000
        self.numInstanceRepeater = varList[4]
        self.numComplex          = varList[5]  ## number of complex entries
        self.numField            = varList[6]  ## number of field entries
        self.lenName             = varList[7]  ## length of name section including padding
        self.lenString           = varList[8]  ## length of string section including padding
        self.numArrayRepeater    = varList[9]
        self.lenPayload          = varList[10] ## length of normal payload section; the start of the array payload section is absStringOffset+lenString+lenPayload
class FieldDescriptor:
    def __init__(self,varList,keywordDict):
        self.name            = keywordDict[varList[0]]
        self.type            = varList[1]
        self.ref             = varList[2] #the field may contain another complex
        self.offset          = varList[3] #offset in payload section; relative to the complex containing it
        self.secondaryOffset = varList[4]

    def getFieldType(self):
        return (self.type >> 4) & 0x1F
class ComplexDescriptor:
    def __init__(self,varList,keywordDict):
        self.name            = keywordDict[varList[0]]
        self.fieldStartIndex = varList[1] #the index of the first field belonging to the complex
        self.numField        = varList[2] #the total number of fields belonging to the complex
        self.alignment       = varList[3]
        self.type            = varList[4]
        self.size            = varList[5] #total length of the complex in the payload section
        self.secondarySize   = varList[6] #seems deprecated
class InstanceRepeater:
    def __init__(self,varList):
        self.null            = varList[0] #called "internalCount", seems to be always null
        self.repetitions     = varList[1] #number of instance repetitions
        self.complexIndex    = varList[2] #index of complex used as the instance
class arrayRepeater:
    def __init__(self,varList):
        self.offset          = varList[0] #offset in array payload section
        self.repetitions     = varList[1] #number of array repetitions
        self.complexIndex    = varList[2] #not necessary for extraction
class Enumeration:
    def __init__(self):
        self.values = dict()
        self.type = 0
class Guid:
    def __init__(self,data,bigEndian):
        #The first 3 elements are native endian and the last one is big endian.
        unpacker=unpackBE if bigEndian else unpackLE
        num1,num2,num3=unpacker("IHH",data[0:8])
        num4=unpackBE("Q",data[8:16])[0]
        self.val=num1,num2,num3,num4
    def __eq__(self,other):
        return self.val==other.val
    def __ne__(self,other):
        return self.val!=other.val
    def __hash__(self):
        return hash(self.val)

    def format(self):
        return "%08x-%04x-%04x-%04x-%010x" % (self.val[0],self.val[1],self.val[2],
                                             (self.val[3]>>48)&0xFFFF,self.val[3]&0x0000FFFFFFFFFF)
    def isNull(self):
        return self.val==(0,0,0,0)

class Complex:
    def __init__(self,desc,dbxhandle):
        self.desc=desc
        self.dbx=dbxhandle #lazy
    def get(self,name):
        pathElems=name.split("/")
        curPos=self
        if pathElems[-1].find("::")!=-1: #grab a complex
            for elem in pathElems:
                try:
                    curPos=curPos.go1(elem)
                except Exception as e:
                    raise Exception("Could not find complex with name: "+str(e)+"\nFull path: "+name+"\nFilename: "+self.dbx.trueFilename)
            return curPos
        #grab a field instead
        for elem in pathElems[:-1]:
            try:
                curPos=curPos.go1(elem)
            except Exception as e:
                raise Exception("Could not find complex with name: "+str(e)+"\nFull path: "+name+"\nFilename: "+self.dbx.trueFilename)
        for field in curPos.fields:
            if field.desc.name==pathElems[-1]:
                return field

        raise Exception("Could not find field with name: "+name+"\nFilename: "+self.dbx.trueFilename)

    def go1(self,name): #go once
        for field in self.fields:
            if field.desc.getFieldType() in (FieldType.ValueType,FieldType.Void,FieldType.Array):
                if field.desc.name+"::"+field.value.desc.name==name:
                    return field.value
        raise Exception(name)


class Field:
    def __init__(self,desc,dbx):
        self.desc=desc
        self.dbx=dbx
    def link(self):
        if self.desc.getFieldType()!=FieldType.Class:
            raise Exception("Invalid link, wrong field type\nField name: "+self.desc.name+"\nField type: "+hex(self.desc.getFieldType())+"\nFile name: "+self.dbx.trueFilename)

        if self.value>>31:
            if self.dbx.ebxRoot=="":
                raise Exception("Ebx root path is not specified!")
            
            extguid=self.dbx.externalGUIDs[self.value&0x7fffffff]

            for existingDbx in dbxArray:
                if existingDbx.fileGUID==extguid[0]:
                    for guid, instance in existingDbx.instances:
                        if guid==extguid[1]:
                            return instance

            f=openEbx(os.path.join(self.dbx.ebxRoot,guidTable[extguid[0]]+".ebx"))
##            print guidTable[extguid[0]]
            dbx=Dbx(f)
            dbxArray.append(dbx)
            for guid, instance in dbx.instances:
                if guid==extguid[1]:
                    return instance
            raise Exception("Nullguid link.\nFilename: "+self.dbx.trueFilename)
        elif self.value!=0:
            for guid, instance in self.dbx.instances:
                if guid==self.dbx.internalGUIDs[self.value-1]:
                    return instance
        else:
            raise Exception("Nullguid link.\nFilename: "+self.dbx.trueFilename)

        raise Exception("Invalid link, could not find target.")

class FieldType:
    Void = 0x0
    DbObject = 0x1
    ValueType = 0x2
    Class = 0x3
    Array = 0x4
    FixedArray = 0x5
    String = 0x6
    CString = 0x7
    Enum = 0x8
    FileRef = 0x9
    Boolean = 0xA
    Int8 = 0xB
    UInt8 = 0xC
    Int16 = 0xD
    UInt16 = 0xE
    Int32 = 0xF
    UInt32 = 0x10
    Int64 = 0x11
    UInt64 = 0x12
    Float32 = 0x13
    Float64 = 0x14
    GUID = 0x15
    SHA1 = 0x16
    
    def __init__(self):
        pass       

def openEbx(fname):
    f=open(fname,"rb")
    if f.read(4) not in (b"\xCE\xD1\xB2\x0F",b"\x0F\xB2\xD1\xCE"):
        f.close()
        raise Exception("Invalid EBX file: "+fname)
    return f

class Stub:
    pass



class Dbx:
    def __init__(self,f,relPath,ebxRoot=""):
        magic=f.read(4)
        if magic==b"\xCE\xD1\xB2\x0F":   self.bigEndian=False
        elif magic==b"\x0F\xB2\xD1\xCE": self.bigEndian=True
        else: raise ValueError("The file is not ebx: "+relPath)

        #Ebx files have platform native endianness.
        self.unpack=unpackBE if self.bigEndian else unpackLE
        self.ebxRoot=ebxRoot
        self.trueFilename=""
        self.header=Header(self.unpack("11I",f.read(44)))
        self.arraySectionstart=self.header.absStringOffset+self.header.lenString+self.header.lenPayload
        self.fileGUID, self.primaryInstanceGUID = Guid(f.read(16),self.bigEndian), Guid(f.read(16),self.bigEndian)
        self.externalGUIDs=[(Guid(f.read(16),self.bigEndian),Guid(f.read(16),self.bigEndian)) for i in range(self.header.numGUID)]
        self.keywords=str.split(f.read(self.header.lenName).decode(),"\0")
        self.keywordDict=dict((hasher(keyword),keyword) for keyword in self.keywords)
        self.fieldDescriptors=[FieldDescriptor(self.unpack("IHHII",f.read(16)), self.keywordDict) for i in range(self.header.numField)]
        self.complexDescriptors=[ComplexDescriptor(self.unpack("IIBBHHH",f.read(16)), self.keywordDict) for i in range(self.header.numComplex)]
        self.instanceRepeaters=[InstanceRepeater(self.unpack("3I",f.read(12))) for i in range(self.header.numInstanceRepeater)]
        while f.tell()%16!=0: f.seek(1,1) #padding
        self.arrayRepeaters=[arrayRepeater(self.unpack("3I",f.read(12))) for i in range(self.header.numArrayRepeater)]
        self.enumerations=dict()

        #payload
        f.seek(self.header.absStringOffset+self.header.lenString)
        self.internalGUIDs=[]
        self.instances=[] # (guid, complex)
        for instanceRepeater in self.instanceRepeaters:
            for repetition in range(instanceRepeater.repetitions):
                instanceGUID=Guid(f.read(16),self.bigEndian)
                self.internalGUIDs.append(instanceGUID)
                if instanceGUID==self.primaryInstanceGUID:
                    self.isPrimaryInstance=True
                else:
                    self.isPrimaryInstance=False
                inst=self.readComplex(instanceRepeater.complexIndex,f)
                inst.guid=instanceGUID

                if self.isPrimaryInstance: self.prim=inst
                self.instances.append((instanceGUID,inst))

        f.close()

        #if no filename found, use the relative input path instead
        #it's just as good though without capitalization
        if self.trueFilename=="":
            self.trueFilename=relPath

    def readComplex(self, complexIndex,f):
        complexDesc=self.complexDescriptors[complexIndex]
        cmplx=Complex(complexDesc,self)

        startPos=f.tell()
        cmplx.fields=[]
        for fieldIndex in range(complexDesc.fieldStartIndex,complexDesc.fieldStartIndex+complexDesc.numField):
            f.seek(startPos+self.fieldDescriptors[fieldIndex].offset)
            cmplx.fields.append(self.readField(fieldIndex,f))

        f.seek(startPos+complexDesc.size)
        return cmplx

    def readField(self,fieldIndex,f):
        fieldDesc=self.fieldDescriptors[fieldIndex]
        field=Field(fieldDesc,self)
        type=fieldDesc.getFieldType()
        
        if type==FieldType.Void:
            # Void (inheritance)
            field.value=self.readComplex(fieldDesc.ref,f)

        elif type==FieldType.ValueType:
            # ValueType
            field.value=self.readComplex(fieldDesc.ref,f)

        elif type==FieldType.Class:
            # Class (reference)
            field.value=self.unpack('I',f.read(4))[0]

        elif type==FieldType.Array:
            # Array
            array_repeater=self.arrayRepeaters[self.unpack("I",f.read(4))[0]]
            array_complex_desc=self.complexDescriptors[fieldDesc.ref]

            f.seek(self.arraySectionstart+array_repeater.offset)
            array_complex=Complex(array_complex_desc,self)
            array_complex.fields=[self.readField(array_complex_desc.fieldStartIndex, f) for repetition in
                                    range(array_repeater.repetitions)]
            field.value=array_complex

        elif type==FieldType.CString:
            # CString
            startPos=f.tell()
            stringOffset=self.unpack("i",f.read(4))[0]
            if stringOffset==-1:
                field.value="*nullString*"
            else:
                f.seek(self.header.absStringOffset+stringOffset)
                data=b""
                while True:
                    a=f.read(1)
                    if a==b"\x00": break
                    data+=a
                field.value=data.decode()
                f.seek(startPos+4)

                if self.isPrimaryInstance and fieldDesc.name=="Name" and self.trueFilename=="":
                    self.trueFilename=field.value

        elif type==FieldType.Enum:
            # Enum
            compareValue=self.unpack("i",f.read(4))[0]
            enumComplex=self.complexDescriptors[fieldDesc.ref]

            if fieldDesc.ref not in self.enumerations:
                enumeration=Enumeration()
                enumeration.type=fieldDesc.ref

                for i in range(enumComplex.fieldStartIndex,enumComplex.fieldStartIndex+enumComplex.numField):
                    enumeration.values[self.fieldDescriptors[i].offset]=self.fieldDescriptors[i].name

                self.enumerations[fieldDesc.ref]=enumeration

            if compareValue not in self.enumerations[fieldDesc.ref].values:
                field.value='*nullEnum*'
            else:
                field.value=self.enumerations[fieldDesc.ref].values[compareValue]

        elif type==FieldType.FileRef:
            # FileRef
            startPos=f.tell()
            stringOffset=self.unpack("i",f.read(4))[0]
            if stringOffset==-1:
                field.value="*nullRef*"
            else:
                f.seek(self.header.absStringOffset + stringOffset)
                data=b""
                while True:
                    a=f.read(1)
                    if a==b"\x00": break
                    data+=a
                field.value=data.decode()
                f.seek(startPos+4)

                if self.isPrimaryInstance and fieldDesc.name=="Name" and self.trueFilename=="":
                    self.trueFilename=field.value

        elif type==FieldType.Boolean:
            # Boolean
            field.value=self.unpack('?',f.read(1))[0]

        elif type==FieldType.Int8:
            # Int8
            field.value=self.unpack('b',f.read(1))[0]

        elif type==FieldType.UInt8:
            # UInt8
            field.value=self.unpack('B',f.read(1))[0]

        elif type==FieldType.Int16:
            # Int16
            field.value=self.unpack('h',f.read(2))[0]

        elif type==FieldType.UInt16:
            # UInt16
            field.value=self.unpack('H',f.read(2))[0]

        elif type==FieldType.Int32:
            # Int32
            field.value=self.unpack('i',f.read(4))[0]

        elif type==FieldType.UInt32:
            # UInt32
            field.value=self.unpack('I',f.read(4))[0]

        elif type==FieldType.Int64:
            # Int64
            field.value=self.unpack('q',f.read(8))[0]

        elif type==FieldType.UInt64:
            # UInt64
            field.value=self.unpack('Q',f.read(8))[0]

        elif type==FieldType.Float32:
            # Float32
            field.value=self.unpack('f',f.read(4))[0]

        elif type==FieldType.Float64:
            # Float64
            field.value=self.unpack('d',f.read(8))[0]

        elif type==FieldType.GUID:
            # Guid
            field.value=Guid(f.read(16),self.bigEndian)

        elif type==FieldType.SHA1:
            # SHA1
            field.value=f.read(20)

        else:
            # Unknown
            raise Exception("Unknown field type 0x%02x" % type)

        return field

    def dump(self,outputFolder):
        print(self.trueFilename)
        outName=os.path.join(outputFolder,self.trueFilename+".txt")
        makeDirs(outName)
        f2=open(outName,"w")
        IGNOREINSTANCES=[]

        for (guid,instance) in self.instances:
            if instance.desc.name not in IGNOREINSTANCES:
                if guid==self.primaryInstanceGUID: self.writeInstance(f2,instance,guid.format()+ " #primary instance")
                else: self.writeInstance(f2,instance,guid.format())
                self.recurse(instance.fields,f2,0)
        f2.close()

    def recurse(self, fields, f2, lvl): #over fields
        lvl+=1
        for field in fields:
            type=field.desc.getFieldType()

            if type in (FieldType.Void,FieldType.ValueType):
                self.writeField(f2,field,lvl,"::"+field.value.desc.name)
                self.recurse(field.value.fields,f2,lvl)

            elif type==FieldType.Class:
                towrite=""
                if field.value>>31:
                    extguid=self.externalGUIDs[field.value&0x7fffffff]
                    try: towrite=guidTable[extguid[0]]+"/"+extguid[1].format()
                    except: towrite=extguid[0].format()+"/"+extguid[1].format()
                elif field.value==0:
                    towrite="*nullGuid*"
                else:
                    intGuid=self.internalGUIDs[field.value-1]
                    towrite=intGuid.format()
                self.writeField(f2,field,lvl," "+towrite)

            elif type==FieldType.Array:
                if len(field.value.fields)==0:
                    self.writeField(f2,field,lvl," *nullArray*")
                else:
                    self.writeField(f2,field,lvl,"::"+field.value.desc.name)

                    #quick hack so I can add indices to array members while using the same recurse function
                    for index in range(len(field.value.fields)):
                        member=field.value.fields[index]
                        if member.desc.name=="member":
                            desc=copy.deepcopy(member.desc)
                            desc.name="member("+str(index)+")"
                            member.desc=desc
                    self.recurse(field.value.fields,f2,lvl)

            elif type==FieldType.GUID:
                self.writeField(f2,field,lvl," "+field.value.format())

            elif type==FieldType.SHA1:
                self.writeField(f2,field,lvl," "+field.value.hex().upper())

            else:
                self.writeField(f2,field,lvl," "+str(field.value))

    def writeField(self,f,field,lvl,text):
        f.write(lvl*"\t"+field.desc.name+text+"\n")
        
    def writeInstance(self,f,cmplx,text):  
        f.write(cmplx.desc.name+" "+text+"\n")

    def extractAssets(self,chunkFolder,chunkFolder2,resFolder,outputFolder):
        self.chunkFolder=chunkFolder
        self.chunkFolder2=chunkFolder2
        self.outputFolder=outputFolder
        self.resFolder=resFolder

        if self.prim.desc.name=="SoundWaveAsset": self.extractSoundWaveAsset()
        elif self.prim.desc.name=="MovieTextureAsset": self.extractMovieAsset()

    def findRes(self):
        path=os.path.join(self.resFolder,os.path.normpath(self.trueFilename.lower())+".res")
        if not os.path.isfile(path):
            print("Res does not exist: "+self.trueFilename)
            return None
        return path

    def findChunk(self,chnk):
        if chnk.isNull():
            return None

        ChunkId=chnk.format()
        chnkPath=os.path.join(self.chunkFolder,ChunkId+".chunk")
        if os.path.isfile(chnkPath):
            return chnkPath
        chnkPath=os.path.join(self.chunkFolder2,ChunkId+".chunk")
        if os.path.isfile(chnkPath):
            return chnkPath
        
        print("Chunk does not exist: "+ChunkId)
        return None

    def extractSPS(self,f,offset,target):
        f.seek(offset)
        if f.read(1)!=b"\x48":
            raise Exception("Wrong SPS header.")

        # Create the target file.
        targetFolder=os.path.dirname(target)
        if not os.path.isdir(targetFolder): os.makedirs(targetFolder)
        f2=open(target,"wb")

        # 0x48=header, 0x44=normal block, 0x45=last block (empty)
        while True:
            f.seek(offset)
            blockStart=unpack(">I",f.read(4))[0]
            blockId=(blockStart&0xFF000000)>>24
            blockSize=blockStart&0x00FFFFFF

            f.seek(offset)
            f2.write(f.read(blockSize))
            offset+=blockSize

            if blockId==0x45:
                break

        f2.close()

    def extractSoundWaveAsset(self):
        print(self.trueFilename)
        histogram=dict() #count the number of times each chunk is used by a variation to obtain the right index

        Chunks=[]
        for i in self.prim.get("$::SoundDataAsset/Chunks::array").fields:
            chnk=Stub()
            Chunks.append(chnk)
            chnk.ChunkId=i.value.get("ChunkId").value
            chnk.ChunkSize=i.value.get("ChunkSize").value

        variations=[i.link() for i in self.prim.get("Variations::array").fields]

        Variations=[]

        for var in variations:
            Variation=Stub()
            Variations.append(Variation)
            Variation.ChunkIndex=var.get("ChunkIndex").value
##            Variation.SeekTablesSize=var.get("SeekTablesSize").value
            Variation.FirstLoopSegmentIndex=var.get("FirstLoopSegmentIndex").value
            Variation.LastLoopSegmentIndex=var.get("LastLoopSegmentIndex").value


            Variation.Segments=[]
            segs=var.get("Segments::array").fields
            for seg in segs:
                Segment=Stub()
                Variation.Segments.append(Segment)
                Segment.SamplesOffset = seg.value.get("SamplesOffset").value
                Segment.SeekTableOffset = seg.value.get("SeekTableOffset").value
                Segment.SegmentLength = seg.value.get("SegmentLength").value

            Variation.ChunkId=Chunks[Variation.ChunkIndex].ChunkId
            Variation.ChunkSize=Chunks[Variation.ChunkIndex].ChunkSize


            #find the appropriate index
            #the index from the Variations array can get large very fast
            #instead, make my own index starting from 0 for every chunkIndex
            if Variation.ChunkIndex in histogram: #has been used previously already
                Variation.Index=histogram[Variation.ChunkIndex]
                histogram[Variation.ChunkIndex]+=1
            else:
                Variation.Index=0
                histogram[Variation.ChunkIndex]=1


        #everything is laid out neatly now
        #Variation fields: ChunkId, ChunkSize, Index, ChunkIndex, SeekTablesSize, FirstLoopSegmentIndex, LastLoopSegmentIndex, Segments
        #Variation.Segments fields: SamplesOffset, SeekTableOffset, SegmentLength

        ChunkHandles=dict() #for each ebx, keep track of all file handles
        for Variation in Variations:
            try:
                f=ChunkHandles[Variation.ChunkId]
            except:
                currentChunkName=self.findChunk(Variation.ChunkId)
                if not currentChunkName:
                    continue

                f=open(currentChunkName,"rb")
                ChunkHandles[Variation.ChunkId]=f
                #print("Chunk found: "+currentChunkName)
        
            for ijk in range(len(Variation.Segments)):
                Segment=Variation.Segments[ijk]
                offset=Segment.SamplesOffset

                target=os.path.join(self.outputFolder,self.trueFilename)
                if len(Chunks)>1 or len(Variations)>1 or len(Variation.Segments)>1:
                    target+=" "+str(Variation.ChunkIndex)+" "+str(Variation.Index)+" "+str(ijk)
                target+=".sps"

                self.extractSPS(f,offset,target)

        for key in ChunkHandles:
            ChunkHandles[key].close()

    def extractMovieAsset(self):
        print(self.trueFilename)

        chnk=self.prim.get("ChunkGuid").value
        if chnk.isNull():
            filename=self.findRes()
            if not filename:
                return
        else:
            filename=self.findChunk(chnk)      
            if not filename:
                return

        target=os.path.join(self.outputFolder,self.trueFilename)+".vp6"
        makeDirs(target)
        shutil.copyfile(filename,target)
