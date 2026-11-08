/* SPDX-License-Identifier: GPL-2.0+ */
#include <assert.h>
#include <stdio.h>
#include "boot_policy.h"
static int start_failure, selected, fail_file, loads, boots, matches_called;
static int start(void) { return start_failure; }
static int match(unsigned int n) { ++matches_called; return (int)n == selected; }
static int load(unsigned int n, int which) { assert((int)n == selected); ++loads; return which == fail_file; }
static void boot(void) { ++boots; }
int main(void)
{
    struct n1_boot_ops ops = { start, match, load, boot };
    for (int s = -1; s < 4; ++s) for (int f = -1; f < 2; ++f) for (int bad = 0; bad < 2; ++bad) {
        selected=s; fail_file=f; start_failure=bad; loads=boots=matches_called=0;
        int result=n1_boot_policy(&ops);
        if (bad) { assert(result==N1_USB_FAILED && !loads && !boots && !matches_called); }
        else if (s<0) { assert(result==N1_NOT_FOUND && matches_called==4 && !loads && !boots); }
        else if (f>=0) { assert(result==N1_FILE_FAILED && !boots && matches_called==s+1); }
        else { assert(result==N1_BOOT_RETURNED && boots==1 && loads==2 && matches_called==s+1); }
    }
    puts("PASS: 30 USB start/not-found/file-failure/returning-boot scenarios; no fallback interface");
    return 0;
}
