/* G83/HS A010D BIOS 8.90. Only the GPP_E4 GPE enable bit is writable here. */
DefinitionBlock ("", "SSDT", 2, "SYMFIX", "GPPE4FIX", 1)
{
    External (\LPCB, FieldUnitObj)
    External (\_SB.GGPE, MethodObj)

    Scope (\)
    {
        OperationRegion (E4GR, SystemMemory, (\LPCB + 0x006A014C), 0x24)
        Field (E4GR, DWordAcc, NoLock, Preserve)
        {
            E4ST, 32,
            Offset (0x20),
            E4ER, 32
        }
        Field (E4GR, DWordAcc, NoLock, Preserve)
        {
            Offset (0x20),
                , 4,
            E4EN, 1
        }

        OperationRegion (E4PR, SystemMemory, (\LPCB + 0x006A0AB0), 4)
        Field (E4PR, DWordAcc, NoLock, Preserve)
        {
            E4CF, 32
        }

        Method (E4LG, 2, Serialized)
        {
            Store (Concatenate (Concatenate (Concatenate ("GPPE4FIX ", Arg0), " "),
                ToHexString (Arg1)), Debug)
        }
    }

    If (\LPCB == 0xFD000000)
    {
        // Check the native GPE route and GPIO input/level/SCI configuration.
        If ((\_SB.GGPE (0x090E0004) == 0x6F) && ((E4CF & 0x06BE3F00) == 0x00080100))
        {
            E4LG ("before", E4ER)
            E4LG ("status", E4ST)
#ifdef E4_DISABLE
            Store (Zero, E4EN)
#endif
#ifdef E4_RESTORE
            Store (One, E4EN)
#endif
            E4LG ("after", E4ER)
        }
    }
}
