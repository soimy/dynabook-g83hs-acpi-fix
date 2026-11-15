/* Entirely simulated namespace: never load this fixture on real hardware. */
DefinitionBlock ("", "DSDT", 2, "SYMFIX", "CHRGTEST", 0x00000001)
{
    External (\_SB.PC00.LPCB.EC.CHRG, DeviceObj)
    Scope (\_SB)
    {
        Device (PC00)
        {
            Name (_HID, "TEST0001")
            Device (LPCB)
            {
                Name (_HID, "TEST0002")
                Device (EC)
                {
                    Name (_HID, "TEST0003")
                    Name (BEFR, Zero)
                    Name (AFTR, Zero)
                    Device (ADP1) { Name (_HID, "TEST0004") }
                    Device (BAT1) { Name (_HID, "TEST0005") }
                    Method (_Q01, 0, Serialized)
                    {
                        BEFR = One
                        Notify (ADP1, 0x80)
                        Notify (BAT1, 0x81)
                        Notify (CHRG, 0x80)
                        AFTR = One
                    }
                }
            }
        }
        Device (LID)
        {
            Name (_HID, "PNP0C0D")
            Method (_LID, 0, NotSerialized) { Return (One) }
        }
    }
}
