"""Read SCIA.OpenAPI.dll by reflection and print the IL of chosen methods (how docs/verify/calc-run.md read SCIA's own code).

Appended sections print ResultsAPI, Structure, CalculationRunner, UniqueIdMappingTables, AdmToAdmServiceWrapper.
"""
import sys; from pathlib import Path; sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from llsm.scia.loader import load
load()
import clr
from System.Reflection import BindingFlags as BF
from System.Reflection.Emit import OpCodes, OperandType
FULL = False
flags = BF.Public | BF.NonPublic | BF.Instance | BF.Static | BF.DeclaredOnly
one, two = {}, {}
for f in clr.GetClrType(OpCodes).GetFields():
    oc = f.GetValue(None); v = oc.Value & 0xFFFF
    (one if oc.Size == 1 else two)[v & 0xFF] = oc
def dis(m):
    body = m.GetMethodBody()
    if body is None: print('   <no body>'); return
    il = bytes(body.GetILAsByteArray()); mod = m.Module; i = 0
    m.DeclaringType.GetGenericArguments() if m.DeclaringType.IsGenericType else None
    while i < len(il):
        b = il[i]; i += 1
        if b == 0xFE: oc = two[il[i]]; i += 1
        else: oc = one[b]
        ot = oc.OperandType; s = ''
        if ot == OperandType.InlineNone: pass
        elif ot in (OperandType.ShortInlineBrTarget, OperandType.ShortInlineI, OperandType.ShortInlineVar): s = str(il[i]); i += 1
        elif ot == OperandType.InlineVar: i += 2
        elif ot in (OperandType.InlineI8, OperandType.InlineR): i += 8
        elif ot == OperandType.InlineSwitch:
            n = int.from_bytes(il[i:i+4], 'little'); i += 4 + 4*n
        else:
            tok = int.from_bytes(il[i:i+4], 'little'); i += 4
            try:
                if ot == OperandType.InlineString: s = repr(mod.ResolveString(tok))
                elif ot in (OperandType.InlineMethod, OperandType.InlineTok, OperandType.InlineType, OperandType.InlineField):
                    mem = mod.ResolveMember(tok); s = f'{mem.DeclaringType.FullName if mem.DeclaringType else ""}::{mem.ToString()}'
                else: s = str(tok)
            except Exception as e: s = f'tok {tok:x} ({type(e).__name__})'
        if FULL or (s and oc.Name not in ("br.s","brtrue.s","brfalse.s","br","brtrue","brfalse","leave.s","leave","ldc.i4.s","ldc.i4","ldloc.s","stloc.s","ldloca.s","ldarg.s")): print('   ', oc.Name, s)
import SCIA.OpenAPI as O, SCIA.OpenAPI.Results as R
def run(T, names):
    t = clr.GetClrType(T)
    for m in list(t.GetMethods(flags)) + list(t.GetConstructors(flags)):
        if names == ['*'] or m.Name in names:
            print('##', t.Name, m.ToString()); dis(m)
run(R.ResultsAPI, ['LoadResult', 'ConvertKey', 'GetCaseDsId', 'GetElementDsId', 'ModifyResultTypeForRequest'])
print('=================== Structure')
t = clr.GetClrType(O.Structure)
for m in t.GetMethods(flags):
    if m.Name in ('RunCalculation', 'FindLoadCaseDsId', 'FindBeamDsId', 'GetDsId', 'LogError') or 'DsId' in m.Name and m.Name.startswith('Get'):
        print('##', m.ToString()); dis(m)
print([m.Name for m in t.GetMethods(flags) if 'Id' in m.Name or 'Calc' in m.Name or 'Result' in m.Name])
print([f.ToString() for f in t.GetFields(flags)])
print('=================== deep')
asm = clr.GetClrType(O.Structure).Assembly
for tn in ('SCIA.OpenAPI.ResultsCalculation.CalculationRunner', 'SCIA.OpenAPI.UniqueIdMappingTables'):
    t = asm.GetType(tn); print('#### TYPE', tn, [f.ToString() for f in t.GetFields(flags)])
    for m in list(t.GetMethods(flags)):
        print('##', m.ToString()); dis(m)
print([x.FullName for x in asm.GetTypes() if 'ResultsCalculation' in x.FullName or 'Mapping' in x.FullName])
print('=================== full')
FULL = True
run(R.ResultsAPI, ['LoadResult'])
t = asm.GetType('SCIA.OpenAPI.ResultsCalculation.CalculationRunner')
for m in t.GetMethods(flags):
    if m.Name == 'RunCalculation': print('##', m.ToString()); dis(m)
FULL = False
for tn, names in (('SCIA.OpenAPI.Structure', ['InitializeResultsAPI', 'LogFailedResult']), ('SCIA.OpenAPI.EsaProject', ['RunCalculation', 'CreateMesh']), ('SCIA.OpenAPI.ResultsCalculation.XmlRequest', ['*'])):
    t = asm.GetType(tn)
    for m in list(t.GetMethods(flags)) + list(t.GetConstructors(flags)):
        if names == ['*'] or m.Name in names: print('##', t.Name, m.ToString()); dis(m)
print('=================== full2')
FULL = True
t = asm.GetType('SCIA.OpenAPI.Structure')
for m in t.GetMethods(flags):
    if m.Name == 'RunCalculation': print('##', m.ToString()); dis(m)
t = asm.GetType('SCIA.OpenAPI.AdmToAdmServiceWrapper')
for m in t.GetMethods(flags):
    if m.Name in ('SendTaskResultTableAndWaitForResponse', 'InitializeRAPI', 'GetMappingTableUniqueID'): print('##', m.ToString()); dis(m)
FULL = False
for tn in ('SCIA.OpenAPI.ResultsCalculation.Property.SelectedCaseCombiClass', 'SCIA.OpenAPI.ResultsCalculation.Property.TypeOfLoad'):
    t = asm.GetType(tn)
    for m in list(t.GetMethods(flags)) + list(t.GetConstructors(flags)): print('##', t.Name, m.ToString()); dis(m)
print('=================== full3')
t = asm.GetType('SCIA.OpenAPI.AdmToAdmServiceWrapper')
for m in t.GetMethods(flags):
    if m.Name in ('RunCalculation', 'CreateMesh', 'ExecuteCommand', 'RunCommand'): print('##', m.ToString()); dis(m)
print([m.Name for m in t.GetMethods(flags) if any(k in m.Name for k in ('Calc','Mesh','Command','Result','Task'))])
for tn in ('SCIA.OpenAPI.ResultsCalculation.CalculationRunner','SCIA.OpenAPI.ResultsCalculation.XmlRequest','SCIA.OpenAPI.ResultsCalculation.ResultPaletteItem'):
    print(tn, [c.ToString() for c in asm.GetType(tn).GetConstructors(flags)])
