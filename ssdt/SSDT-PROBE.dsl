/* Temporary readout probe; _Q01 is called only when its CHRG target exists. */
DefinitionBlock ("", "SSDT", 2, "SYMFIX", "CHRGTEST", 0x00000001)
{
    External (\_SB.PC00.LPCB.EC.CHRG, DeviceObj)
    External (\_SB.PC00.LPCB.EC._Q01, MethodObj)
    Scope (\_SB)
    {
        Device (CQPR)
        {
            Name (_HID, "SYMF0002")
            Name (PASS, Zero)
            Method (_STA, 0, NotSerialized) { Return (PASS) }
        }
    }
    If (CondRefOf (\_SB.PC00.LPCB.EC.CHRG))
    {
        \_SB.PC00.LPCB.EC._Q01 ()
        \_SB.CQPR.PASS = 0x0F
    }
}
