/* SPDX-License-Identifier: GPL-2.0+ */
/* Shared production policy and fault-injection test; no MMC/network interface. */
struct n1_boot_ops {
    int (*usb_start)(void);
    int (*matches)(unsigned int);
    int (*load_verified)(unsigned int, int);
    void (*boot)(void);
};
enum { N1_USB_FAILED = 1, N1_NOT_FOUND, N1_FILE_FAILED, N1_BOOT_RETURNED };
static int n1_boot_policy(const struct n1_boot_ops *ops)
{
    unsigned int disk;
    if (ops->usb_start())
        return N1_USB_FAILED;
    for (disk = 0; disk < 4; ++disk) {
        if (!ops->matches(disk))
            continue;
        if (ops->load_verified(disk, 0) || ops->load_verified(disk, 1))
            return N1_FILE_FAILED;
        ops->boot();
        return N1_BOOT_RETURNED; /* A returning kernel launch is always a failure. */
    }
    return N1_NOT_FOUND;
}
