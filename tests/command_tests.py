# Copyright (C) 2017 Red Hat, Inc., Bryn M. Reeves <bmr@redhat.com>
#
# command_tests.py - Boom command API tests.
#
# This file is part of the boom project.
#
# This copyrighted material is made available to anyone wishing to use,
# modify, copy, or redistribute it subject to the terms and conditions
# of the GNU General Public License v.2.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program; if not, write to the Free Software Foundation,
# Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
import unittest
import logging
from sys import stdout
from os import listdir
from os.path import exists, abspath
import re

# Python3 moves StringIO to io
try:
    from StringIO import StringIO
except:
    from io import StringIO

log = logging.getLogger()
log.level = logging.DEBUG
log.addHandler(logging.FileHandler("test.log"))

from boom import *
from boom.osprofile import *
from boom.bootloader import *
from boom.command import *
from boom.config import *
from boom.report import *

BOOT_ROOT_TEST = abspath("./tests")
config = BoomConfig()
config.legacy_enable = False
config.legacy_sync = False
set_boom_config(config)
set_boot_path(BOOT_ROOT_TEST)


class CommandTests(unittest.TestCase):
    def test_int_if_val_with_val(self):
        import boom.command
        val = "1"
        self.assertEqual(boom.command._int_if_val(val), int(val))

    def test_int_if_val_with_none(self):
        import boom.command
        val = None
        self.assertEqual(boom.command._int_if_val(val), None)

    def test_int_if_val_with_badint(self):
        import boom.command
        val = "qux"
        with self.assertRaises(ValueError) as cm:
            boom.command._int_if_val(val)

    def test_subvol_from_arg_subvol(self):
        import boom.command
        xtuple = ("/svol", None)
        self.assertEqual(boom.command._subvol_from_arg("/svol"), xtuple)

    def test_subvol_from_arg_subvolid(self):
        import boom.command
        xtuple = (None, "23")
        self.assertEqual(boom.command._subvol_from_arg("23"), xtuple)

    def test_subvol_from_arg_none(self):
        import boom.command
        self.assertEqual(boom.command._subvol_from_arg(None), (None, None))

    def test_list_entries(self):
        path = boom_entries_path()
        nr = len([p for p in listdir(path) if p.endswith(".conf")])
        bes = list_entries()
        self.assertTrue(len(bes), nr)

    def test_list_entries_match_machine_id(self):
        machine_id = "611f38fd887d41dea7eb3403b2730a76"
        path = boom_entries_path()
        nr = len([p for p in listdir(path) if p.startswith(machine_id)])
        bes = list_entries(Selection(machine_id=machine_id))
        self.assertTrue(len(bes), nr)

    def test_list_entries_match_version(self):
        version = "4.10.17-100.fc24.x86_64"
        path = boom_entries_path()
        nr = len([p for p in listdir(path) if version in p])
        bes = list_entries(Selection(version=version))
        self.assertEqual(len(bes), nr)

    def test_create_entry_notitle(self):
        # Fedora 24 (Workstation Edition)
        osp = get_os_profile_by_id("9cb53ddda889d6285fd9ab985a4c47025884999f")
        osp.title = None
        with self.assertRaises(ValueError) as cm:
            be = create_entry(None, "2.6.0", "ffffffff", "/dev/vg_hex/root",
                              lvm_root_lv="vg_hex/root", profile=osp)

    def test_create_entry_noversion(self):
        # Fedora 24 (Workstation Edition)
        osp = get_os_profile_by_id("9cb53ddda889d6285fd9ab985a4c47025884999f")
        with self.assertRaises(ValueError) as cm:
            be = create_entry("ATITLE", None, "ffffffff", "/dev/vg_hex/root",
                              lvm_root_lv="vg_hex/root", profile=osp)

    def test_create_entry_nomachineid(self):
        # Fedora 24 (Workstation Edition)
        osp = get_os_profile_by_id("9cb53ddda889d6285fd9ab985a4c47025884999f")
        with self.assertRaises(ValueError) as cm:
            be = create_entry("ATITLE", "2.6.0", "", "/dev/vg_hex/root",
                              lvm_root_lv="vg_hex/root", profile=osp)

    def test_create_entry_norootdevice(self):
        # Fedora 24 (Workstation Edition)
        osp = get_os_profile_by_id("9cb53ddda889d6285fd9ab985a4c47025884999f")
        with self.assertRaises(ValueError) as cm:
            be = create_entry("ATITLE", "2.6.0", "ffffffff", None,
                              lvm_root_lv="vg_hex/root", profile=osp)

    def test_create_entry_noosprofile(self):
        # Fedora 24 (Workstation Edition)
        osp = get_os_profile_by_id("9cb53ddda889d6285fd9ab985a4c47025884999f")
        with self.assertRaises(ValueError) as cm:
            be = create_entry("ATITLE", "2.6.0", "ffffffff",
                              "/dev/vg_hex/root", lvm_root_lv="vg_hex/root")

    def test_create_dupe(self):
        # Fedora 24 (Workstation Edition)
        osp = get_os_profile_by_id("9cb53ddda889d6285fd9ab985a4c47025884999f")

        title = "Fedora (4.1.1-100.fc24.x86_64) 24 (Workstation Edition)"
        machine_id = "611f38fd887d41dea7eb3403b2730a76"
        version = "4.1.1-100.fc24"
        root_device = "/dev/sda5"
        btrfs_subvol_id = "23"

        with self.assertRaises(ValueError) as cm:
            create_entry(title, version, machine_id, root_device,
                         btrfs_subvol_id=btrfs_subvol_id, profile=osp,
                         allow_no_dev=True)

    def test_create_delete_entry(self):
        # Fedora 24 (Workstation Edition)
        osp = get_os_profile_by_id("9cb53ddda889d6285fd9ab985a4c47025884999f")
        be = create_entry("ATITLE", "2.6.0", "ffffffff", "/dev/vg_hex/root",
                          lvm_root_lv="vg_hex/root", profile=osp)
        self.assertTrue(exists(be._entry_path))

        delete_entries(Selection(boot_id=be.boot_id))
        self.assertFalse(exists(be._entry_path))

    def test_delete_entries_no_matching_raises(self):
        with self.assertRaises(IndexError) as cm:
            delete_entries(Selection(boot_id="thereisnospoon"))

    def test_print_entries_no_matching(self):
        xoutput = r"BootID.*Version.*Name.*RootDevice"
        output = StringIO()
        opts = BoomReportOpts(report_file=output)
        print_entries(selection=Selection(boot_id="thereisnoboot"), opts=opts)
        self.assertTrue(re.match(xoutput, output.getvalue()))

    def test_print_entries_default_stdout(self):
        print_entries()

    def test_print_entries_boot_id_filter(self):
        xoutput = [r"BootID.*Version.*Name.*RootDevice",
                   r"debfd7f.*4.11.12-100.fc24.x86_64.*Fedora.*"
                   r"/dev/vg00/lvol0-snapshot"]
        output = StringIO()
        opts = BoomReportOpts(report_file=output)
        print_entries(selection=Selection(boot_id="debfd7f"), opts=opts)
        print(output.getvalue())
        for pair in zip(xoutput, output.getvalue().splitlines()):
            self.assertTrue(re.match(pair[0], pair[1]))

# Calling the main() entry point from the test suite causes a SysExit
# exception in ArgParse() (too few arguments).
#    def test_boom_main_noargs(self):
#        args = [abspath('bin/boom'), '--help']
#        main(args)

# vim: set et ts=4 sw=4 :
