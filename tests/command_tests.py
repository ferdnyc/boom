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
from os import listdir, makedirs
from os.path import abspath, exists, join
import shutil
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

# For access to non-exported members
import boom.command

from tests import *

BOOT_ROOT_TEST = abspath("./tests")
config = BoomConfig()
config.legacy_enable = False
config.legacy_sync = False
set_boom_config(config)
set_boot_path(BOOT_ROOT_TEST)

debug_masks = ['profile', 'entry', 'report', 'command', 'all']


class CommandHelperTests(unittest.TestCase):
    """Test internal boom.command helpers: methods in this part of the
        test suite import boom.command directly in order to access the
        non-public helper routines not included in __all__.
    """
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

    def test_str_indent(self):
        import boom.command
        instr = "1\n2\n3\n4"
        xstr = "    1\n    2\n    3\n    4"
        indent = 4
        outstr = boom.command._str_indent(instr, indent)
        self.assertEqual(outstr, xstr)

    def test_str_indent_bad_indent(self):
        import boom.command
        instr = "1\n2\n3\n4"
        indent = "qux"
        with self.assertRaises(TypeError) as cm:
            outstr = boom.command._str_indent(instr, indent)

    def test_str_indent_bad_str(self):
        import boom.command
        instr = None
        indent = 4
        with self.assertRaises(AttributeError) as cm:
            outstr = boom.command._str_indent(instr, indent)

    def test_canonicalize_lv_name(self):
        import boom.command
        xlv = "vg/lv"
        for lvstr in  ["vg/lv", "/dev/vg/lv"]:
            self.assertEqual(xlv, boom.command._canonicalize_lv_name(lvstr))

    def test_canonicalize_lv_name_bad_lv(self):
        import boom.command
        with self.assertRaises(ValueError) as cm:
            boom.command._canonicalize_lv_name("vg/lv/foo/bar/baz")
        with self.assertRaises(ValueError) as cm:
            boom.command._canonicalize_lv_name("vg-lv")
        with self.assertRaises(ValueError) as cm:
            boom.command._canonicalize_lv_name("/dev/mapper/vg-lv")

    def test_expand_fields_defaults(self):
        import boom.command
        default = "f1,f2,f3"
        xfield = default
        self.assertEqual(xfield, boom.command._expand_fields(default, ""))

    def test_expand_fields_replace(self):
        import boom.command
        default = "f1,f2,f3"
        options = "f4,f5,f6"
        xfield = options
        self.assertEqual(xfield, boom.command._expand_fields(default, options))

    def test_expand_fields_add(self):
        import boom.command
        default = "f1,f2,f3"
        options = "+f4,f5,f6"
        xfield = default + ',' + options[1:]
        self.assertEqual(xfield, boom.command._expand_fields(default, options))

    def test_set_debug_no_debug_arg(self):
        """Test set_debug() with an empty debug mask argument.
        """
        import boom.command
        boom.command.set_debug(None)

    def test_set_debug_args_one(self):
        """Test set_debug() with a single debug mask argument.
        """
        import boom.command
        for mask in debug_masks:
            boom.command.set_debug(mask)

    def test_set_debug_args_all(self):
        """Test set_debug() with a list of debug mask arguments.
        """
        import boom.command
        all_masks = ",".join(debug_masks[:-1])
        boom.command.set_debug(all_masks)

    def test_set_debug_no_debug_arg(self):
        """Test set_debug() with a bad debug mask argument.
        """
        import boom.command
        with self.assertRaises(ValueError) as cm:
            boom.command.set_debug("nosuchmask")

    def test_setup_logging(self):
        """Test the setup_logging() command helper.
        """
        import boom.command
        args = MockArgs()
        boom.command.setup_logging(args)

    def test_show_legacy_default(self):
        """Test the show_legacy() command helper.
        """
        import boom.command
        boom.command.show_legacy()


# Default test OsProfile identifiers
test_os_id = "9cb53ddda889d6285fd9ab985a4c47025884999f"
test_os_disp_id = test_os_id[0:6]


def get_create_cmd_args():
    """Return a correct MockArgs object for a call to the _create_cmd()
        helper. Tests that should fail modify the fields returned to
        generate the required error.
    """
    args = MockArgs()
    args.profile = test_os_disp_id
    args.title = "ATITLE"
    args.version = "2.6.0"
    args.machine_id = "ffffffff"
    args.root_device = "/dev/vg_hex/root"
    args.root_lv = "vg_hex/root"
    return args


class CommandTests(unittest.TestCase):
    """Test boom.command APIs
    """

    # Master BLS loader directory for sandbox
    loader_path = join(BOOT_ROOT_TEST, "loader")

    # Master boom configuration path for sandbox
    boom_path = join(BOOT_ROOT_TEST, "boom")

    # Master grub configuration path for sandbox
    grub_path = join(BOOT_ROOT_TEST, "grub")

    # Test fixture init/cleanup
    def setUp(self):
        """Set up a test fixture for the CommandTests class.

            Defines standard objects for use in these tests.
        """
        reset_sandbox()

        # Sandbox paths
        boot_sandbox = join(SANDBOX_PATH, "boot")
        boom_sandbox = join(SANDBOX_PATH, "boot/boom")
        grub_sandbox = join(SANDBOX_PATH, "boot/grub")
        loader_sandbox = join(SANDBOX_PATH, "boot/loader")

        # Initialise sandbox from master
        makedirs(boot_sandbox)
        shutil.copytree(self.boom_path, boom_sandbox)
        shutil.copytree(self.loader_path, loader_sandbox)
        shutil.copytree(self.grub_path, grub_sandbox)

        # Set boom paths
        set_boot_path(boot_sandbox)

        # Load test OsProfile and BootEntry data
        load_profiles()
        load_entries()

    def tearDown(self):
        # Drop any in-memory entries and profiles modified by tests
        drop_entries()
        drop_profiles()

        # Clear sandbox data
        rm_sandbox()
        reset_boom_paths()

    def test_command_find_profile_with_profile_arg(self):
        import boom.command
        _find_profile = boom.command._find_profile
        cmd_args = MockArgs()
        cmd_args.profile = "d4439b7d2f928c39f1160c0b0291407e5990b9e0" # F26
        cmd_args.machine_id = "12345" # No HostProfile
        osp = _find_profile(cmd_args, "", cmd_args.machine_id, "test")
        self.assertEqual(osp.os_id, cmd_args.profile)

    def test_command_find_profile_with_version_arg(self):
        import boom.command
        _find_profile = boom.command._find_profile
        cmd_args = MockArgs()
        cmd_args.profile = None
        cmd_args.version = "4.16.11-100.fc26.x86_64" # F26
        cmd_args.machine_id = "12345" # No HostProfile
        xprofile = "d4439b7d2f928c39f1160c0b0291407e5990b9e0"
        osp = _find_profile(cmd_args, cmd_args.version,
                            cmd_args.machine_id, "test")
        self.assertEqual(osp.os_id, xprofile)

    def test_command_find_profile_with_bad_version_arg(self):
        import boom.command
        _find_profile = boom.command._find_profile
        cmd_args = MockArgs()
        cmd_args.profile = None
        cmd_args.version = "4.16.11-100.x86_64" # no match
        cmd_args.machine_id = "12345" # No HostProfile
        xprofile = "d4439b7d2f928c39f1160c0b0291407e5990b9e0"
        osp = _find_profile(cmd_args, "", cmd_args.machine_id, "test")
        self.assertEqual(osp, None)

    def test_command_find_profile_bad_profile(self):
        import boom.command
        _find_profile = boom.command._find_profile
        cmd_args = MockArgs()
        cmd_args.profile = "quxquxquxquxquxquxquxqux" # nonexistent
        cmd_args.machine_id = "12345" # No HostProfile
        osp = _find_profile(cmd_args, "", cmd_args.machine_id, "test")
        self.assertEqual(osp, None)

    def test_command_find_profile_ambiguous_profile(self):
        import boom.command
        _find_profile = boom.command._find_profile
        cmd_args = MockArgs()
        cmd_args.profile = "9" # ambiguous
        cmd_args.machine_id = "12345" # No HostProfile
        osp = _find_profile(cmd_args, "", cmd_args.machine_id, "test")
        self.assertEqual(osp, None)

    def test_command_find_profile_ambiguous_host(self):
        import boom.command
        _find_profile = boom.command._find_profile
        cmd_args = MockArgs()
        cmd_args.profile = ""
        cmd_args.machine_id = "fffffffffff" # Ambiguous HostProfile
        osp = _find_profile(cmd_args, "", cmd_args.machine_id, "test")
        self.assertEqual(osp, None)

    def test_command_find_profile_host(self):
        import boom.command
        _find_profile = boom.command._find_profile
        cmd_args = MockArgs()
        cmd_args.profile = ""
        cmd_args.machine_id = "ffffffffffffc"
        cmd_args.label = ""
        hp = _find_profile(cmd_args, "", cmd_args.machine_id, "test")
        self.assertTrue(hp)
        self.assertTrue(hasattr(hp, "add_opts"))

    def test_command_find_profile_host_os_mismatch(self):
        import boom.command
        _find_profile = boom.command._find_profile
        cmd_args = MockArgs()
        cmd_args.profile = "3fc389bba581e5b20c6a46c7fc31b04be465e973"
        cmd_args.machine_id = "ffffffffffffc"
        cmd_args.label = ""
        hp = _find_profile(cmd_args, "", cmd_args.machine_id, "test")
        self.assertFalse(hp)

    def test_command_find_profile_no_matching(self):
        import boom.command
        _find_profile = boom.command._find_profile
        cmd_args = MockArgs()
        cmd_args.profile = ""
        cmd_args.machine_id = "1111111111111111" # no matching
        hp = _find_profile(cmd_args, "", cmd_args.machine_id,
                           "test", optional=False)
        self.assertFalse(hp)

    #
    # API call tests
    #
    # BootEntry tests
    #

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
        osp = get_os_profile_by_id(test_os_id)
        osp.title = None
        with self.assertRaises(ValueError) as cm:
            be = create_entry(None, "2.6.0", "ffffffff", "/dev/vg_hex/root",
                              lvm_root_lv="vg_hex/root", profile=osp)

    def test_create_entry_noversion(self):
        # Fedora 24 (Workstation Edition)
        osp = get_os_profile_by_id(test_os_id)
        with self.assertRaises(ValueError) as cm:
            be = create_entry("ATITLE", None, "ffffffff", "/dev/vg_hex/root",
                              lvm_root_lv="vg_hex/root", profile=osp)

    def test_create_entry_nomachineid(self):
        # Fedora 24 (Workstation Edition)
        osp = get_os_profile_by_id(test_os_id)
        with self.assertRaises(ValueError) as cm:
            be = create_entry("ATITLE", "2.6.0", "", "/dev/vg_hex/root",
                              lvm_root_lv="vg_hex/root", profile=osp)

    def test_create_entry_norootdevice(self):
        # FIXME: should this default from the lvm_root_lv?
        # Fedora 24 (Workstation Edition)
        osp = get_os_profile_by_id(test_os_id)
        with self.assertRaises(ValueError) as cm:
            be = create_entry("ATITLE", "2.6.0", "ffffffff", None,
                              lvm_root_lv="vg_hex/root", profile=osp)

    def test_create_entry_noosprofile(self):
        # Fedora 24 (Workstation Edition)
        osp = get_os_profile_by_id(test_os_id)
        with self.assertRaises(ValueError) as cm:
            be = create_entry("ATITLE", "2.6.0", "ffffffff",
                              "/dev/vg_hex/root", lvm_root_lv="vg_hex/root")

    def test_create_dupe(self):
        # Fedora 24 (Workstation Edition)
        osp = get_os_profile_by_id(test_os_id)

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
        osp = get_os_profile_by_id(test_os_id)
        be = create_entry("ATITLE", "2.6.0", "ffffffff", "/dev/vg_hex/root",
                          lvm_root_lv="vg_hex/root", profile=osp)
        self.assertTrue(exists(be._entry_path))

        delete_entries(Selection(boot_id=be.boot_id))
        self.assertFalse(exists(be._entry_path))

    def test_create_delete_entry_with_legacy(self):
        config = BoomConfig()
        config.legacy_enable = True
        config.legacy_sync = True
        set_boom_config(config)
        set_boot_path(BOOT_ROOT_TEST)

        # Fedora 24 (Workstation Edition)
        osp = get_os_profile_by_id(test_os_id)
        be = create_entry("ATITLE", "2.6.0", "ffffffff", "/dev/vg_hex/root",
                          lvm_root_lv="vg_hex/root", profile=osp)
        self.assertTrue(exists(be._entry_path))

        delete_entries(Selection(boot_id=be.boot_id))
        self.assertFalse(exists(be._entry_path))


    def test_delete_entries_no_matching_raises(self):
        with self.assertRaises(IndexError) as cm:
            delete_entries(Selection(boot_id="thereisnospoon"))

    def test_clone_entry_no_boot_id(self):
        with self.assertRaises(ValueError) as cm:
            bad_be = clone_entry(Selection())

    def test_clone_entry_no_matching_boot_id(self):
        with self.assertRaises(ValueError) as cm:
            bad_be = clone_entry(Selection(boot_id="qqqqqqq"), title="FAIL")

    def test_clone_entry_ambiguous_boot_id(self):
        with self.assertRaises(ValueError) as cm:
            bad_be = clone_entry(Selection(boot_id="6"), title="NEWTITLE")


    def test_clone_entry_add_opts(self):
        be = clone_entry(Selection(boot_id="9591d36"), title="NEWNEWTITLE",
                         add_opts="foo", allow_no_dev=True)
        self.assertTrue(exists(be._entry_path))
        be.delete_entry()
        self.assertFalse(exists(be._entry_path))

    def test_clone_entry_del_opts(self):
        be = clone_entry(Selection(boot_id="9591d36"), title="NEWNEWTITLE",
                         del_opts="rhgb quiet", allow_no_dev=True)
        self.assertTrue(exists(be._entry_path))
        be.delete_entry()
        self.assertFalse(exists(be._entry_path))

    def test_clone_delete_entry(self):
        # Fedora 24 (Workstation Edition)
        osp = get_os_profile_by_id(test_os_id)
        be = create_entry("ATITLE", "2.6.0", "ffffffff", "/dev/vg_hex/root",
                          lvm_root_lv="vg_hex/root", profile=osp)
        self.assertTrue(exists(be._entry_path))

        be2 = clone_entry(Selection(boot_id=be.boot_id), title="ANEWTITLE",
                          version="2.6.1")

        self.assertTrue(exists(be2._entry_path))

        be.delete_entry()
        be2.delete_entry()

        self.assertFalse(exists(be._entry_path))
        self.assertFalse(exists(be2._entry_path))

    def test_clone_entry_no_args(self):
        # Fedora 24 (Workstation Edition)
        osp = get_os_profile_by_id(test_os_id)
        be = create_entry("ATITLE", "2.6.0", "ffffffff", "/dev/vg_hex/root",
                          lvm_root_lv="vg_hex/root", profile=osp)
        self.assertTrue(exists(be._entry_path))

        with self.assertRaises(ValueError) as cm:
            be2 = clone_entry(Selection(boot_id=be.boot_id))

        be.delete_entry()

    def test_clone_entry_with_add_del_opts(self):
        # Entry with options +"debug" -"rhgb quiet"
        orig_boot_id = "78861b7"
        be = clone_entry(Selection(boot_id=orig_boot_id),
                         title="clone with addopts")
        orig_be = find_entries(Selection(boot_id=orig_boot_id))[0]
        self.assertTrue(orig_be)
        self.assertTrue(be)
        self.assertEqual(orig_be.options, be.options)
        be.delete_entry()

    def test_clone_dupe(self):
        # Fedora 24 (Workstation Edition)
        osp = get_os_profile_by_id(test_os_id)
        be = create_entry("CLONE_TEST", "2.6.0", "ffffffff", "/dev/vg_hex/root",
                          lvm_root_lv="vg_hex/root", profile=osp)
        self.assertTrue(exists(be._entry_path))

        be2 = clone_entry(Selection(boot_id=be.boot_id), title="ANEWTITLE",
                          version="2.6.1")

        with self.assertRaises(ValueError) as cm:
            be3 = clone_entry(Selection(boot_id=be.boot_id), title="ANEWTITLE",
                              version="2.6.1")

        be.delete_entry()
        be2.delete_entry()

    def test_edit_entry_no_boot_id(self):
        with self.assertRaises(ValueError) as cm:
            bad_be = edit_entry(Selection())

    def test_edit_entry_no_matching_boot_id(self):
        with self.assertRaises(ValueError) as cm:
            bad_be = edit_entry(Selection(boot_id="qqqqqqq"), title="FAIL")

    def test_edit_entry_ambiguous_boot_id(self):
        with self.assertRaises(ValueError) as cm:
            bad_be = edit_entry(Selection(boot_id="6"), title="NEWTITLE")


    def test_edit_entry_add_opts(self):
        # Fedora 24 (Workstation Edition)
        osp = get_os_profile_by_id(test_os_id)
        orig_be = create_entry("EDIT_TEST", "2.6.0", "ffffffff",
                               "/dev/vg_hex/root", lvm_root_lv="vg_hex/root",
                               profile=osp)

        # Confirm original entry has been written
        self.assertTrue(exists(orig_be._entry_path))

        # Save these - they will be overwritten by the edit operation
        orig_id = orig_be.boot_id
        orig_entry_path = orig_be._entry_path

        edit_title = "EDITED_TITLE"
        edit_add_opts = "foo"

        # FIXME: restore allow_no_dev
        edit_be = edit_entry(Selection(boot_id=orig_id), title=edit_title,
                              add_opts=edit_add_opts)

        # Confirm edited entry has been written
        self.assertTrue(exists(edit_be._entry_path))

        # Confirm original entry has been removed
        self.assertFalse(exists(orig_entry_path))

        # Verify new boot_id
        self.assertFalse(orig_id == edit_be.boot_id)

        # Verify edited title and options
        self.assertEqual(edit_title, edit_be.title)
        self.assertEqual(edit_be.bp.add_opts, [edit_add_opts])
        self.assertTrue(edit_add_opts in edit_be.options)

        # Clean up entries
        edit_be.delete_entry()

    def test_edit_entry_add_opts_with_add_opts(self):
        edit_title = "EDITED_TITLE"
        edit_add_opts = "foo"
        orig_add_opts = "bar"

        # Fedora 24 (Workstation Edition)
        osp = get_os_profile_by_id(test_os_id)
        orig_be = create_entry("EDIT_TEST", "2.6.0", "ffffffff",
                               "/dev/vg_hex/root", lvm_root_lv="vg_hex/root",
                               add_opts="bar", profile=osp)

        # Confirm original entry has been written
        self.assertTrue(exists(orig_be._entry_path))

        # Save these - they will be overwritten by the edit operation
        orig_id = orig_be.boot_id
        orig_entry_path = orig_be._entry_path

        # FIXME: restore allow_no_dev
        edit_be = edit_entry(Selection(boot_id=orig_id), title=edit_title,
                             add_opts=edit_add_opts)

        # Confirm edited entry has been written
        self.assertTrue(exists(edit_be._entry_path))

        # Confirm original entry has been removed
        self.assertFalse(exists(orig_entry_path))

        # Verify new boot_id
        self.assertFalse(orig_id == edit_be.boot_id)

        # Verify edited title and options
        self.assertEqual(edit_title, edit_be.title)
        self.assertEqual(edit_be.bp.add_opts, [edit_add_opts, orig_add_opts])
        # Verify original added opts
        self.assertTrue(orig_add_opts in edit_be.options)
        # Verify edit added opts
        self.assertTrue(edit_add_opts in edit_be.options)

        # Clean up entries
        edit_be.delete_entry()

    def test_edit_entry_del_opts(self):
        # Fedora 24 (Workstation Edition)
        osp = get_os_profile_by_id(test_os_id)
        orig_be = create_entry("EDIT_TEST", "2.6.0", "ffffffff",
                               "/dev/vg_hex/root", lvm_root_lv="vg_hex/root",
                               profile=osp)

        # Confirm original entry has been written
        self.assertTrue(exists(orig_be._entry_path))

        # Save these - they will be overwritten by the edit operation
        orig_id = orig_be.boot_id
        orig_entry_path = orig_be._entry_path

        edit_title = "EDITED_TITLE"
        edit_del_opts = "rhgb"

        # FIXME: restore allow_no_dev
        edit_be = edit_entry(Selection(boot_id=orig_id), title=edit_title,
                             del_opts=edit_del_opts)

        # Confirm edited entry has been written
        self.assertTrue(exists(edit_be._entry_path))

        # Confirm original entry has been removed
        self.assertFalse(exists(orig_entry_path))

        # Verify new boot_id
        self.assertFalse(orig_id == edit_be.boot_id)

        # Verify edited title and options
        self.assertEqual(edit_title, edit_be.title)
        self.assertEqual(edit_be.bp.del_opts, [edit_del_opts])
        self.assertTrue(edit_del_opts not in edit_be.options)

        # Clean up entries
        edit_be.delete_entry()

    def test_edit_entry_del_opts_with_del_opts(self):
        edit_title = "EDITED_TITLE"
        edit_del_opts = "rhgb"
        orig_del_opts = "quiet"

        # Fedora 24 (Workstation Edition)
        osp = get_os_profile_by_id(test_os_id)
        orig_be = create_entry("EDIT_TEST", "2.6.0", "ffffffff",
                               "/dev/vg_hex/root", lvm_root_lv="vg_hex/root",
                               del_opts="quiet", profile=osp)

        # Confirm original entry has been written
        self.assertTrue(exists(orig_be._entry_path))

        # Save these - they will be overwritten by the edit operation
        orig_id = orig_be.boot_id
        orig_entry_path = orig_be._entry_path

        # Verify original deled opts
        self.assertTrue(orig_del_opts not in orig_be.options)
        self.assertEqual(orig_be.bp.del_opts, [orig_del_opts])

        # FIXME: restore allow_no_dev
        edit_be = edit_entry(Selection(boot_id=orig_id), title=edit_title,
                             del_opts=edit_del_opts)

        # Confirm edited entry has been written
        self.assertTrue(exists(edit_be._entry_path))

        # Confirm original entry has been removed
        self.assertFalse(exists(orig_entry_path))

        # Verify new boot_id
        self.assertFalse(orig_id == edit_be.boot_id)

        # Verify edited title and options
        self.assertEqual(edit_title, edit_be.title)
        self.assertEqual(edit_be.bp.del_opts, [edit_del_opts, orig_del_opts])
        # Verify original deleted opts
        self.assertTrue(orig_del_opts not in edit_be.options)
        # Verify edit deleted opts
        self.assertTrue(edit_del_opts not in edit_be.options)

        # Clean up entries
        edit_be.delete_entry()

    def test_edit_entry_del_opts(self):
        # Fedora 24 (Workstation Edition)
        osp = get_os_profile_by_id(test_os_id)
        orig_be = create_entry("EDIT_TEST", "2.6.0", "ffffffff",
                               "/dev/vg_hex/root", lvm_root_lv="vg_hex/root",
                               profile=osp)

        be = edit_entry(Selection(boot_id=orig_be.boot_id),
                        title="NEWNEWTITLE", del_opts="rhgb quiet")

        self.assertTrue(exists(be._entry_path))
        be.delete_entry()
        self.assertFalse(exists(be._entry_path))

    def test_edit_delete_entry(self):
        # Fedora 24 (Workstation Edition)
        osp = get_os_profile_by_id(test_os_id)
        orig_be = create_entry("ATITLE", "2.6.0", "ffffffff",
                               "/dev/vg_hex/root", lvm_root_lv="vg_hex/root",
                               profile=osp)
        orig_path = orig_be._entry_path
        self.assertTrue(exists(orig_path))

        edit_be = edit_entry(Selection(boot_id=orig_be.boot_id),
                             title="ANEWTITLE", version="2.6.1")

        self.assertTrue(exists(edit_be._entry_path))
        self.assertFalse(exists(orig_path))

        edit_be.delete_entry()

        self.assertFalse(exists(edit_be._entry_path))

    def test_edit_entry_no_args(self):
        # Fedora 24 (Workstation Edition)
        osp = get_os_profile_by_id(test_os_id)
        be = create_entry("ATITLE", "2.6.0", "ffffffff", "/dev/vg_hex/root",
                          lvm_root_lv="vg_hex/root", profile=osp)
        self.assertTrue(exists(be._entry_path))

        with self.assertRaises(ValueError) as cm:
            be2 = edit_entry(Selection(boot_id=be.boot_id))

        be.delete_entry()

    def test_edit_entry_with_add_del_opts(self):
        # Fedora 24 (Workstation Edition)
        osp = get_os_profile_by_id(test_os_id)
        orig_be = create_entry("EDIT_TEST", "2.6.0", "ffffffff",
                               "/dev/vg_hex/root", lvm_root_lv="vg_hex/root",
                               profile=osp)
        orig_path = orig_be._entry_path

        add_opts = "debug"
        del_opts = "rhgb quiet"

        # Entry with options +"debug" -"rhgb quiet"
        orig_boot_id = orig_be.boot_id
        edit_be = edit_entry(Selection(boot_id=orig_boot_id),
                             title="edit with addopts", add_opts=add_opts,
                             del_opts=del_opts)

        self.assertTrue(edit_be)

        self.assertTrue(exists(edit_be._entry_path))
        self.assertFalse(exists(orig_path))

        self.assertTrue(add_opts in edit_be.options)
        self.assertTrue(del_opts not in edit_be.options)

        edit_be.delete_entry()

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
        for pair in zip(xoutput, output.getvalue().splitlines()):
            self.assertTrue(re.match(pair[0], pair[1]))

    #
    # API call tests
    #
    # OsProfile tests
    #

    def test_command_create_delete_profile(self):
        osp = create_profile("Some Distro", "somedist", "1 (Qunk)", "1",
                             uname_pattern="sd1",
                             kernel_pattern="/vmlinuz-%{version}",
                             initramfs_pattern="/initramfs-%{version}.img",
                             root_opts_lvm2="rd.lvm.lv=%{lvm_root_lv}",
                             root_opts_btrfs="rootflags=%{btrfs_subvolume}",
                             options="root=%{root_device} %{root_opts}")
        self.assertTrue(osp)
        self.assertEqual(osp.os_name, "Some Distro")

        # Use the OsProfile.delete_profile() method
        osp.delete_profile()

    def test_command_create_delete_profiles(self):
        osp = create_profile("Some Distro", "somedist", "1 (Qunk)", "1",
                             uname_pattern="sd1",
                             kernel_pattern="/vmlinuz-%{version}",
                             initramfs_pattern="/initramfs-%{version}.img",
                             root_opts_lvm2="rd.lvm.lv=%{lvm_root_lv}",
                             root_opts_btrfs="rootflags=%{btrfs_subvolume}",
                             options="root=%{root_device} %{root_opts}")

        self.assertTrue(osp)
        self.assertEqual(osp.os_name, "Some Distro")

        # Use the command.delete_profiles() API call
        delete_profiles(selection=Selection(os_id=osp.os_id))

    def test_command_delete_profiles_no_match(self):
        with self.assertRaises(IndexError) as cm:
            delete_profiles(selection=Selection(os_id="XyZZy"))

    def test_command_create_delete_profile_from_file(self):
        os_release_path = "tests/os-release/fedora26-test-os-release"
        osp = create_profile(None, None, None, None,
                             profile_file=os_release_path, uname_pattern="sd1",
                             kernel_pattern="/vmlinuz-%{version}",
                             initramfs_pattern="/initramfs-%{version}.img",
                             root_opts_lvm2="rd.lvm.lv=%{lvm_root_lv}",
                             root_opts_btrfs="rootflags=%{btrfs_subvolume}",
                             options="root=%{root_device} %{root_opts}")
        self.assertTrue(osp)
        self.assertEqual(osp.os_name, "Fedora")
        self.assertEqual(osp.os_version, "26 (Testing Edition)")
        osp.delete_profile()

    def test_command_create_delete_profile_from_data(self):
        profile_data = {
            BOOM_OS_NAME: "Some Distro", BOOM_OS_SHORT_NAME: "somedist",
            BOOM_OS_VERSION: "1 (Qunk)", BOOM_OS_VERSION_ID: "1",
            BOOM_OS_UNAME_PATTERN: "sd1",
            BOOM_OS_KERNEL_PATTERN: "/vmlinuz-%{version}",
            BOOM_OS_INITRAMFS_PATTERN: "/initramfs-%{version}.img",
            BOOM_OS_ROOT_OPTS_LVM2: "rd.lvm.lv=%{lvm_root_lv}",
            BOOM_OS_ROOT_OPTS_BTRFS: "rootflags=%{btrfs_subvolume}",
            BOOM_OS_OPTIONS: "root=%{root_device} %{root_opts}",
            BOOM_OS_TITLE: "This is a title (%{version})"
        }

        # All fields: success
        osp = create_profile(None, None, None, None, profile_data=profile_data)
        self.assertTrue(osp)
        self.assertEqual(osp.os_name, "Some Distro")
        self.assertEqual(osp.os_version, "1 (Qunk)")
        osp.delete_profile()

        # Pop identity fields in reverse checking order:
        # OS_VERSION_ID, OS_VERSION, OS_SHORT_NAME, OS_NAME

        profile_data.pop(BOOM_OS_VERSION_ID)
        with self.assertRaises(ValueError) as cm:
            bad_osp = create_profile(None, None, None, None,
                                     profile_data=profile_data)

        profile_data.pop(BOOM_OS_VERSION)
        with self.assertRaises(ValueError) as cm:
            bad_osp = create_profile(None, None, None, None,
                                     profile_data=profile_data)

        profile_data.pop(BOOM_OS_SHORT_NAME)
        with self.assertRaises(ValueError) as cm:
            bad_osp = create_profile(None, None, None, None,
                                     profile_data=profile_data)

        profile_data.pop(BOOM_OS_NAME)
        with self.assertRaises(ValueError) as cm:
            bad_osp = create_profile(None, None, None, None,
                                     profile_data=profile_data)

    def test__create_profile_cmd_invalid_identifier(self):
        """Test that _create_profile_cmd() rejects an identifier arg.
        """
        args = MockArgs()
        identifier = "d4439b7"
        r = boom.command._create_profile_cmd(args, None, None, identifier)
        self.assertEqual(r, 1)

    def test__create_profile_cmd_from_host(self):
        args = MockArgs()
        args.from_host = True
        args.uname_pattern = "test1"

        # Workaround the possibility that the host profile already exists.
        boom.command._delete_profile_cmd(args, None, None, "d4439b7")

        r = boom.command._create_profile_cmd(args, None, None, None)
        self.assertEqual(r, 0)

    def test__delete_profile_cmd_valid_identifier(self):
        """Test that _delete_profile_cmd() deletes a profile via a
            valid identifier arg.
        """
        args = MockArgs()
        identifier = "d4439b7"
        r = boom.command._delete_profile_cmd(args, None, None, identifier)
        self.assertEqual(r, 0)

    def test_clone_profile_no_os_id(self):
        with self.assertRaises(ValueError) as cm:
            bad_osp = clone_profile(Selection())

    def test_clone_profile_no_args(self):
        with self.assertRaises(ValueError) as cm:
            bad_osp = clone_profile(Selection(os_id="d4439b7"))

    def test_clone_profile_no_matching_os_id(self):
        with self.assertRaises(ValueError) as cm:
            bad_osp = clone_profile(Selection(os_id="fffffff"), name="NEW")

    def test_clone_profile_ambiguous_os_id(self):
        with self.assertRaises(ValueError) as cm:
            bad_osp = clone_profile(Selection(os_id="d"), name="NEW")

    def test_clone_profile_new_name(self):
        osp = clone_profile(Selection(os_id="d4439b7"),
                            name="NEW", short_name="new", version="26 (Not)",
                            version_id="~26")
        self.assertTrue(osp)
        self.assertEqual("NEW", osp.os_name)
        self.assertEqual("new", osp.os_short_name)
        osp.delete_profile()

    def test_create_edit_profile(self):
        osp = create_profile("Test1", "test", "1 (Test)", "1",
                             uname_pattern="t1")

        self.assertTrue(osp)

        edit_osp = edit_profile(Selection(os_id=osp.os_id),
                                uname_pattern="t2")

        self.assertTrue(edit_osp)
        self.assertEqual(osp.uname_pattern, "t2")
        osp.delete_profile()
        edit_osp.delete_profile()

    def test_edit_no_matching_os_id(self):
        with self.assertRaises(ValueError) as cm:
            edit_osp = edit_profile(Selection(os_id="notfound"),
                                    uname_pattern="nf2")

    def test_edit_ambiguous_os_id(self):
        with self.assertRaises(ValueError) as cm:
            edit_osp = edit_profile(Selection(os_id="d"),
                                    uname_pattern="d2")

    def test_list_profiles(self):
        profiles = list_profiles()
        self.assertTrue(profiles)

    def test_print_profiles(self):
        repstr = print_profiles()

    #
    # API call tests
    #
    # HostProfile tests
    #

    def test_create_delete_host(self):
        osp = create_profile("Some Distro", "somedist", "1 (Qunk)", "1",
                             uname_pattern="sd1",
                             kernel_pattern="/vmlinuz-%{version}",
                             initramfs_pattern="/initramfs-%{version}.img",
                             root_opts_lvm2="rd.lvm.lv=%{lvm_root_lv}",
                             root_opts_btrfs="rootflags=%{btrfs_subvolume}",
                             options="root=%{root_device} %{root_opts}")

        self.assertTrue(osp)
        self.assertEqual(osp.os_name, "Some Distro")

        host_machine_id = "ffffffffffffffff1234567890"
        host_name = "somehost.somedomain"
        host_opts = osp.options + " hostoptions"

        hp = create_host(machine_id=host_machine_id, host_name=host_name,
                         os_id=osp.os_id, label="", options=host_opts)

        self.assertEqual(host_machine_id, hp.machine_id)
        self.assertEqual(host_name, hp.host_name)
        self.assertEqual(host_opts, hp.options)

        # Use the command.delete_hosts() API call
        delete_hosts(Selection(host_id=hp.host_id))

        # Clean up osp
        osp.delete_profile()

    def test_create_host_no_os_id(self):
        os_id = None
        host_machine_id = "ffffffffffffffff1234567890"
        host_name = "somehost.somedomain"
        host_opts = "hostoptions"

        with self.assertRaises(ValueError) as cm:
            bad_hp = create_host(machine_id=host_machine_id,
                                 host_name=host_name, os_id=os_id,
                                 label="", options=host_opts)

    def test_create_host_no_os_id_match(self):
        os_id = "notfound"
        host_machine_id = "ffffffffffffffff1234567890"
        host_name = "somehost.somedomain"
        host_opts = "hostoptions"

        with self.assertRaises(ValueError) as cm:
            bad_hp = create_host(machine_id=host_machine_id,
                                 host_name=host_name, os_id=os_id,
                                 label="", options=host_opts)

    def test_create_host_no_host_name(self):
        osp = create_profile("Some Distro", "somedist", "1 (Qunk)", "1",
                             uname_pattern="sd1",
                             kernel_pattern="/vmlinuz-%{version}",
                             initramfs_pattern="/initramfs-%{version}.img",
                             root_opts_lvm2="rd.lvm.lv=%{lvm_root_lv}",
                             root_opts_btrfs="rootflags=%{btrfs_subvolume}",
                             options="root=%{root_device} %{root_opts}")

        self.assertTrue(osp)
        self.assertEqual(osp.os_name, "Some Distro")

        host_machine_id = "ffffffffffffffff1234567890"
        host_name = ""
        host_opts = "hostoptions"

        with self.assertRaises(ValueError) as cm:
            bad_hp = create_host(machine_id=host_machine_id,
                                 host_name=host_name, os_id=osp.os_id,
                                 label="", options=host_opts)

        osp.delete_profile()

    def test_create_host_no_machine_id(self):
        osp = create_profile("Some Distro", "somedist", "1 (Qunk)", "1",
                             uname_pattern="sd1",
                             kernel_pattern="/vmlinuz-%{version}",
                             initramfs_pattern="/initramfs-%{version}.img",
                             root_opts_lvm2="rd.lvm.lv=%{lvm_root_lv}",
                             root_opts_btrfs="rootflags=%{btrfs_subvolume}",
                             options="root=%{root_device} %{root_opts}")

        self.assertTrue(osp)
        self.assertEqual(osp.os_name, "Some Distro")

        host_machine_id = ""
        host_name = "somehost.somedomain"
        host_opts = "hostoptions"

        with self.assertRaises(ValueError) as cm:
            bad_hp = create_host(machine_id=host_machine_id,
                                 host_name=host_name, os_id=osp.os_id,
                                 label="", options=host_opts)

        osp.delete_profile()

    def test_create_host_all_args(self):
        osp = create_profile("Some Distro", "somedist", "1 (Qunk)", "1",
                             uname_pattern="sd1",
                             kernel_pattern="/vmlinuz-%{version}",
                             initramfs_pattern="/initramfs-%{version}.img",
                             root_opts_lvm2="rd.lvm.lv=%{lvm_root_lv}",
                             root_opts_btrfs="rootflags=%{btrfs_subvolume}",
                             options="root=%{root_device} %{root_opts}")

        self.assertTrue(osp)
        self.assertEqual(osp.os_name, "Some Distro")

        host_machine_id = "ffffffffffffffff1234567890"
        host_name = "somehost.somedomain"

        hp = create_host(machine_id=host_machine_id, host_name=host_name,
                         os_id=osp.os_id, label="label",
                         kernel_pattern="/vmlinuz",
                         initramfs_pattern="/initramfs.img",
                         root_opts_lvm2="rd.lvm.lv=vg/lv",
                         root_opts_btrfs="rootflags=subvolid=1",
                         options=osp.options, add_opts="debug",
                         del_opts="rhgb quiet")

        self.assertEqual(host_machine_id, hp.machine_id)
        self.assertEqual(host_name, hp.host_name)

        hp.delete_profile()

        # Clean up osp
        osp.delete_profile()

    def test_delete_hosts_no_match(self):
        with self.assertRaises(IndexError) as cm:
            delete_hosts(Selection(host_id="nomatch"))

    def test_clone_host(self):
        osp = create_profile("Some Distro", "somedist", "1 (Qunk)", "1",
                             uname_pattern="sd1",
                             kernel_pattern="/vmlinuz-%{version}",
                             initramfs_pattern="/initramfs-%{version}.img",
                             root_opts_lvm2="rd.lvm.lv=%{lvm_root_lv}",
                             root_opts_btrfs="rootflags=%{btrfs_subvolume}",
                             options="root=%{root_device} %{root_opts}")

        self.assertTrue(osp)
        self.assertEqual(osp.os_name, "Some Distro")

        host_machine_id = "ffffffffffffffff1234567890"
        clone_machine_id = "ffffffffffffffff0987654321"
        host_name = "somehost.somedomain"
        host_opts = osp.options + " hostoptions"

        hp = create_host(machine_id=host_machine_id, host_name=host_name,
                         os_id=osp.os_id, label="", options=host_opts)

        self.assertEqual(host_machine_id, hp.machine_id)
        self.assertEqual(host_name, hp.host_name)
        self.assertEqual(host_opts, hp.options)

        clone_hp = clone_host(Selection(host_id=hp.host_id),
                              machine_id=clone_machine_id)

        self.assertEqual(clone_machine_id, clone_hp.machine_id)
        self.assertNotEqual(hp.host_id, clone_hp.host_id)

        hp.delete_profile()
        clone_hp.delete_profile()

        # Clean up osp
        osp.delete_profile()

    def test_clone_host_no_host_id(self):
        with self.assertRaises(ValueError) as cm:
            bad_hp = clone_host(Selection(host_id=None))

    def test_clone_host_no_host_id_match(self):
        host_id = "notfound"

        with self.assertRaises(ValueError) as cm:
            bad_hp = clone_host(Selection(host_id=host_id),
                                machine_id="ffffffff")

    def test_clone_host_no_args(self):
        host_id = "5ebcb1f"

        with self.assertRaises(ValueError) as cm:
            bad_hp = clone_host(Selection(host_id=host_id))

    def test_create_edit_host(self):
        osp = create_profile("Some Distro", "somedist", "1 (Qunk)", "1",
                             uname_pattern="sd1",
                             kernel_pattern="/vmlinuz-%{version}",
                             initramfs_pattern="/initramfs-%{version}.img",
                             root_opts_lvm2="rd.lvm.lv=%{lvm_root_lv}",
                             root_opts_btrfs="rootflags=%{btrfs_subvolume}",
                             options="root=%{root_device} %{root_opts}")

        self.assertTrue(osp)
        self.assertEqual(osp.os_name, "Some Distro")

        host_machine_id = "ffffffffffffffff1234567890"
        host_name = "somehost.somedomain"
        host_opts = osp.options + " hostoptions"

        hp = create_host(machine_id=host_machine_id, host_name=host_name,
                         os_id=osp.os_id, label="", options=host_opts)

        self.assertEqual(host_machine_id, hp.machine_id)
        self.assertEqual(host_name, hp.host_name)
        self.assertEqual(host_opts, hp.options)

        edit_name = "someother.host"
        edit_opts = osp.options

        edit_hp = edit_host(Selection(host_id=hp.host_id),
                            machine_id=host_machine_id, host_name=edit_name,
                            os_id=osp.os_id, label="", options=edit_opts)

        self.assertEqual(host_machine_id, edit_hp.machine_id)
        self.assertEqual(edit_name, edit_hp.host_name)
        self.assertEqual(osp.options, edit_hp.options)

        edit_hp.delete_profile()

        # Clean up osp
        osp.delete_profile()

    def test_list_hosts_default(self):
        """Test the list_hosts() API call with no selection.
        """
        hps = list_hosts()
        self.assertTrue(len(hps) >= 1)

    def test_print_hosts_default(self):
        """Test the list_hosts() API call with no selection.
        """
        print_hosts()

    #
    # Command handler tests
    #

    def test__create_cmd(self):
        """Test the _create_cmd() handler with correct arguments.
        """
        args = get_create_cmd_args()
        opts = boom.command._report_opts_from_args(args)
        boom.command._create_cmd(args, None, opts, None)

    def test__create_cmd_bad_identity(self):
        """Test the _create_cmd() handler with an invalid identity
            function argument.
        """
        args = get_create_cmd_args()
        opts = boom.command._report_opts_from_args(args)
        r = boom.command._create_cmd(args, None, opts, "badident")
        self.assertEqual(r, 1)

    @unittest.skip("Requires boom.command.get_uts_release() override")
    def test__create_cmd_no_version(self):
        """Test the _create_cmd() handler with missing version.
        """
        args = get_create_cmd_args()
        args.version = None
        opts = boom.command._report_opts_from_args(args)
        r = boom.command._create_cmd(args, None, opts, None)
        self.assertEqual(r, 1)

    def test__create_cmd_version_from_uts(self):
        """Test the _create_cmd() handler with missing version, and the
            default version obtained from the system UTS data.
        """
        args = get_create_cmd_args()
        args.version = None
        opts = boom.command._report_opts_from_args(args)
        r = boom.command._create_cmd(args, None, opts, None)
        self.assertNotEqual(r, 1)

    def test__create_cmd_no_root_device(self):
        """Test the _create_cmd() handler with missing root device.
        """
        args = get_create_cmd_args()
        args.root_device = None
        opts = boom.command._report_opts_from_args(args)
        r = boom.command._create_cmd(args, None, opts, None)
        self.assertEqual(r, 1)

    def test__create_cmd_auto_machine_id(self):
        """Test the _create_cmd() handler with automatic machine_id.
        """
        args = get_create_cmd_args()
        args.machine_id = None
        opts = boom.command._report_opts_from_args(args)
        r = boom.command._create_cmd(args, None, opts, None)
        self.assertNotEqual(r, 1)

    def test__create_cmd_no_profile(self):
        """Test the _create_cmd() handler with missing profile.
        """
        args = get_create_cmd_args()
        args.profile = None
        # Avoid HostProfile match
        args.machine_id = "quxquxquxqux"
        opts = boom.command._report_opts_from_args(args)
        r = boom.command._create_cmd(args, None, opts, None)
        self.assertEqual(r, 1)

    def test__create_cmd_no_title(self):
        """Test the _create_cmd() handler with missing title.
        """
        args = get_create_cmd_args()
        args.title = None

        # Avoid OsProfile auto-title
        osp = get_os_profile_by_id(test_os_id)
        osp.title = None

        opts = boom.command._report_opts_from_args(args)
        r = boom.command._create_cmd(args, None, opts, None)
        self.assertEqual(r, 1)

    def test__delete_cmd(self):
        """Test the _delete_cmd() handler with a valid entry.
        """
        args = MockArgs()
        args.boot_id = "61bcc49"
        opts = boom.command._report_opts_from_args(args)
        r = boom.command._delete_cmd(args, None, opts, None)
        self.assertNotEqual(r, 1)

    def test__delete_cmd_with_options(self):
        """Test the _delete_cmd() handler with a valid entry and report
            field options string.
        """
        args = MockArgs()
        args.boot_id = "61bcc49"
        args.options = "title"
        opts = boom.command._report_opts_from_args(args)
        r = boom.command._delete_cmd(args, None, opts, None)
        self.assertNotEqual(r, 1)

    def test__delete_cmd_verbose(self):
        """Test the _delete_cmd() handler with a valid entry and
            verbose output.
        """
        args = MockArgs()
        args.boot_id = "61bcc49"
        args.verbose = 1
        opts = boom.command._report_opts_from_args(args)
        r = boom.command._delete_cmd(args, None, opts, None)
        self.assertNotEqual(r, 1)

    def test__delete_cmd_identity(self):
        """Test the _delete_cmd() handler with a valid entry that
            is passed via the 'identiry' handler argument.
        """
        args = MockArgs()
        opts = boom.command._report_opts_from_args(args)
        r = boom.command._delete_cmd(args, None, opts, "61bcc49")
        self.assertNotEqual(r, 1)

    def test__delete_cmd_no_criteria(self):
        """Test the _delete_cmd() handler with no valid selection.
        """
        args = MockArgs()
        args.boot_id = None
        opts = boom.command._report_opts_from_args(args)
        r = boom.command._delete_cmd(args, None, opts, None)
        self.assertEqual(r, 1)

    def test__delete_cmd_multi(self):
        """Test the _delete_cmd() handler with multiple valid entries.
        """
        args = MockArgs()
        args.boot_id = "6" # Matches four entries
        opts = boom.command._report_opts_from_args(args)
        r = boom.command._delete_cmd(args, None, opts, None)
        self.assertNotEqual(r, 1)

    def test__delete_cmd_no_matching(self):
        """Test the _delete_cmd() handler with no matching entries.
        """
        args = MockArgs()
        args.boot_id = "qux" # Matches no entries
        opts = boom.command._report_opts_from_args(args)
        r = boom.command._delete_cmd(args, None, opts, None)
        self.assertEqual(r, 1)

    def test__clone_cmd(self):
        """Test the _clone_cmd() handler with a valid entry and new
            title.
        """
        args = MockArgs()
        args.boot_id = "61bcc49"
        args.title = "Something New"
        # Disable device presence checks
        args.no_dev = True
        opts = boom.command._report_opts_from_args(args)
        r = boom.command._clone_cmd(args, None, opts, None)
        self.assertNotEqual(r, 1)

    def test__clone_cmd_no_criteria(self):
        """Test the _clone_cmd() handler with no valid selection.
        """
        args = MockArgs()
        args.boot_id = None
        args.title = "Something New"
        opts = boom.command._report_opts_from_args(args)
        r = boom.command._clone_cmd(args, None, opts, None)
        self.assertEqual(r, 1)

    def test__clone_cmd_no_matching(self):
        """Test the _clone_cmd() handler with no matching entries.
        """
        args = MockArgs()
        args.boot_id = "qux"
        args.title = "Something New"
        opts = boom.command._report_opts_from_args(args)
        r = boom.command._clone_cmd(args, None, opts, None)
        self.assertEqual(r, 1)

    def test__show_cmd(self):
        """Test the _show_cmd() handler.
        """
        args = MockArgs()
        r = boom.command._show_cmd(args, None, None, None)
        self.assertEqual(r, 0)

    def test__show_cmd_single(self):
        """Test the _show_cmd() handler with a single selected entry.
        """
        args = MockArgs()
        args.boot_id = "61bcc49"
        r = boom.command._show_cmd(args, None, None, None)
        self.assertEqual(r, 0)

    def test__show_cmd_single_identifier(self):
        """Test the _show_cmd() handler with a single identifier.
        """
        args = MockArgs()
        r = boom.command._show_cmd(args, None, None, "61bcc49")
        self.assertEqual(r, 0)

    def test__show_cmd_selection(self):
        """Test the _show_cmd() handler with multiple selected entries.
        """
        args = MockArgs()
        args.boot_id = "6" # Matches four entries
        r = boom.command._show_cmd(args, None, None, None)
        self.assertEqual(r, 0)

    def test__show_cmd_invalid_selection(self):
        """Test the _show_cmd() handler with an invalid selection.
        """
        args = MockArgs()
        # Clear boot_id
        args.boot_id = None
        # Invalid selection criteria for BootEntry type
        select = Selection(host_add_opts="qux")
        r = boom.command._show_cmd(args, select, None, None)
        self.assertEqual(r, 1)

    def test__list_cmd(self):
        args = MockArgs()
        r = boom.command._list_cmd(args, None, None, None)
        self.assertNotEqual(r, 1)

    def test__list_cmd_single(self):
        args = MockArgs()
        args.boot_id = "61bcc49"
        r = boom.command._list_cmd(args, None, None, None)
        self.assertNotEqual(r, 1)

    def test__list_cmd_single_identifier(self):
        """Test the _list_cmd() handler with a single identifier.
        """
        args = MockArgs()
        r = boom.command._list_cmd(args, None, None, "61bcc49")
        self.assertEqual(r, 0)

    def test__list_cmd_selection(self):
        """Test the _list_cmd() handler with multiple selected entries.
        """
        args = MockArgs()
        args.boot_id = "6" # Matches four entries
        r = boom.command._list_cmd(args, None, None, None)
        self.assertEqual(r, 0)

    def test__list_cmd_invalid_selection(self):
        """Test the _list_cmd() handler with an invalid selection.
        """
        args = MockArgs()
        # Clear boot_id
        args.boot_id = None
        # Invalid selection criteria for BootEntry type
        select = Selection(host_add_opts="qux")
        r = boom.command._list_cmd(args, select, None, None)
        self.assertEqual(r, 1)

    def test__list_cmd_with_options(self):
        """Test the _list_cmd() handler with report field options
            string.
        """
        args = MockArgs()
        args.options = "title"
        opts = boom.command._report_opts_from_args(args)
        r = boom.command._list_cmd(args, None, opts, None)
        self.assertEqual(r, 0)

    def test__list_cmd_verbose(self):
        """Test the _list_cmd() handler with a valid entry and
            verbose output.
        """
        args = MockArgs()
        args.boot_id = "61bcc49"
        args.verbose = 1
        opts = boom.command._report_opts_from_args(args)
        r = boom.command._list_cmd(args, None, opts, None)
        self.assertEqual(r, 0)

    def test__edit_cmd(self):
        """Test the _edit_cmd() handler with a valid entry and new
            title.
        """
        args = MockArgs()
        args.boot_id = "61bcc49"
        args.title = "Something New"
        # Disable device presence checks
        args.no_dev = True
        opts = boom.command._report_opts_from_args(args)
        r = boom.command._edit_cmd(args, None, opts, None)
        self.assertNotEqual(r, 1)

    def test__edit_cmd_no_criteria(self):
        """Test the _edit_cmd() handler with no valid selection.
        """
        args = MockArgs()
        args.boot_id = None
        args.title = "Something New"
        opts = boom.command._report_opts_from_args(args)
        r = boom.command._edit_cmd(args, None, opts, None)
        self.assertEqual(r, 1)

    def test__edit_cmd_no_matching(self):
        """Test the _edit_cmd() handler with no matching entries.
        """
        args = MockArgs()
        args.boot_id = "qux"
        args.title = "Something New"
        opts = boom.command._report_opts_from_args(args)
        r = boom.command._edit_cmd(args, None, opts, None)
        self.assertEqual(r, 1)

# Calling the main() entry point from the test suite causes a SysExit
# exception in ArgParse() (too few arguments).
#    def test_boom_main_noargs(self):
#        args = [abspath('bin/boom'), '--help']
#        main(args)

# vim: set et ts=4 sw=4 :
