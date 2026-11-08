// SPDX-License-Identifier: GPL-2.0+
#include <cli.h>
#include <command.h>
#include <console.h>
#include <dm.h>
#include <env.h>
#include <hang.h>
#include <stdio.h>
#include <string.h>
#include <vsprintf.h>
#include <wdt.h>
#include <u-boot/sha256.h>
#include "n1_boot_policy.h"
#include "n1_boot_inputs.h"

#define N1_KERNEL_ADDR 0x08080000UL
#define N1_FDT_ADDR    0x08008000UL

static int start_usb(void)
{
    puts("N1 stage B: initializing USB (no internal storage driver)\n");
    return run_command("usb start", 0);
}

static int matches(unsigned int disk)
{
    char cmd[96];
    const char *uuid;
    printf("N1 stage C: checking USB %u root partition identity\n", disk);
    env_set("n1_uuid", NULL);
    snprintf(cmd, sizeof(cmd), "part uuid usb %u:2 n1_uuid", disk);
    if (run_command(cmd, 0))
        return 0;
    uuid = env_get("n1_uuid");
    return uuid && !strcmp(uuid, N1_ROOT_PARTUUID);
}

static int load_verified(unsigned int disk, int dtb)
{
    char cmd[192], *end;
    const char *size_text;
    unsigned char actual[SHA256_SUM_LEN];
    unsigned long address = dtb ? N1_FDT_ADDR : N1_KERNEL_ADDR;
    unsigned long expected_size = dtb ? N1_DTB_SIZE : N1_KERNEL_SIZE;
    const unsigned char *expected_sha = dtb ? n1_dtb_sha : n1_kernel_sha;
    printf("N1 stage D: checking USB %u %s\n", disk, dtb ? "DTB" : "kernel");
    env_set("filesize", NULL);
    /* Bound reads to one byte above the exact expected size. Never execute USB scripts. */
    snprintf(cmd, sizeof(cmd), "fatload usb %u:1 %lx %s %lx", disk, address,
             dtb ? "amlogic.dtb" : "kernel.img", expected_size + 1);
    if (run_command(cmd, 0))
        return -1;
    size_text = env_get("filesize");
    if (!size_text || hextoul(size_text, &end) != expected_size || *end)
        return -1;
    sha256_csum_wd((void *)address, expected_size, actual, CHUNKSZ_SHA256);
    return memcmp(actual, expected_sha, SHA256_SUM_LEN) ? -1 : 0;
}

static void boot_kernel(void)
{
    if (env_set("bootargs", "console=ttyAML0,115200n8 console=tty1 no_console_suspend "
                "root=PARTUUID=" N1_ROOT_PARTUUID " ro rootwait panic=0 init=/sbin/n1-rescue-init"))
        return;
    puts("N1 stage E: entering Linux; root is USB, read-only\n");
    run_command("booti 0x08080000 - 0x08008000", 0);
}

void main_loop(void)
{
    struct udevice *watchdog;
    const struct n1_boot_ops ops = { start_usb, matches, load_verified, boot_kernel };
    int ret;

    puts("N1 stage A: USB-only loader reached; diagnostics build\n");
    cli_init();
    disable_ctrlc(1);
    /* Stop inherited watchdog state explicitly, even if this U-Boot never started it. */
    ret = uclass_get_device(UCLASS_WDT, 0, &watchdog);
    if (!ret)
        ret = wdt_stop(watchdog);
    if (ret) {
        printf("N1 watchdog STOP: error %d; refusing to boot\n", ret);
        hang();
    }
    printf("N1 rescue policy stopped (%d); no fallback and no command prompt\n", n1_boot_policy(&ops));
    hang();
}
