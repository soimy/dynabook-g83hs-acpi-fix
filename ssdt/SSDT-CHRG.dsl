/* Notification-only namespace supplement. No hardware resources or addresses. */
DefinitionBlock ("", "SSDT", 2, "SYMFIX", "CHRGSAFE", 0x00000001)
{
    External (\_SB.PC00.LPCB.EC, DeviceObj)
    External (\_SB.PC00.LPCB.EC.CHRG, DeviceObj)

    Scope (\_SB.PC00.LPCB.EC)
    {
        If (LNot (CondRefOf (CHRG)))
        {
            Device (CHRG)
            {
                /* Private test ID; _STA=0 prevents driver binding/enumeration. */
                Name (_HID, "SYMF0001")
                Name (_STA, Zero)
            }
        }
    }
}
