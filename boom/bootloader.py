# Copyright (C) 2017 Red Hat, Inc., Bryn M. Reeves <bmr@redhat.com>
#
# bootloader.py - Boom BLS bootloader manager
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
"""The ``boom.bootloader`` module defines classes for working with
on-disk boot loader entries: the ``BootEntry`` class represents an
individual boot loader entry, and the ``BootParams`` class
encapsulates the parameters needed to boot an instance of the
operating system. The kernel version and root device configuration
of an existing ``BootEntry`` may be changed by modifying or
substituting its ``BootParams`` object (this may also be used to
'clone' configuration from one entry to another).

Functions are provided to read and write boot loader entries from an
on-disk store (normally located at ``/boot/loader/entries``), and to
retrieve particular ``BootEntry`` objects based on a variety of
selection criteria.

The ``BootEntry`` class includes named properties for each boot entry
attribute ("entry key"). In addition, the class serves as a container
type, allowing attributes to be accessed via dictionary-style indexing.
This simplifies iteration over a profile's key / value pairs and allows
straightforward access to all members in scripts and the Python shell.

All entry key names are made available as named members of the module:
``BOOT_ENTRY_*``, and the ``ENTRY_KEYS`` list. A map of Boom key names
to BLS keys is available in the ``KEY_MAP`` dictionary (a reverse map
is also provided in the ``MAP_KEY`` member).

"""
from __future__ import print_function

from boom import *
from boom.osprofile import *
from boom.hostprofile import find_host_profiles

from os.path import basename, exists as path_exists, join as path_join
from tempfile import mkstemp
from os import listdir, rename, fdopen, chmod, unlink, fdatasync, stat, dup
from stat import S_ISBLK
from hashlib import sha1
import logging
import re

#: The path to the BLS boot entries directory relative to /boot
ENTRIES_PATH = "loader/entries"

#: The format used to construct entry file names.
BOOT_ENTRIES_FORMAT = "%s-%s-%s.conf"

#: A regular expression matching the boom file name format.
BOOT_ENTRIES_PATTERN = r"(\w*)-(\w{1,7})-([a-zA-Z0-9.\-_]*)"

#: The file mode with which BLS entries should be created.
BOOT_ENTRY_MODE = 0o644

#: The ``BootEntry`` title key.
BOOM_ENTRY_TITLE = "BOOM_ENTRY_TITLE"
#: The ``BootEntry`` version key.
BOOM_ENTRY_VERSION = "BOOM_ENTRY_VERSION"
#: The ``BootEntry`` machine_id key.
BOOM_ENTRY_MACHINE_ID = "BOOM_ENTRY_MACHINE_ID"
#: The ``BootEntry`` linux key.
BOOM_ENTRY_LINUX = "BOOM_ENTRY_LINUX"
#: The ``BootEntry`` initrd key.
BOOM_ENTRY_INITRD = "BOOM_ENTRY_INITRD"
#: The ``BootEntry`` efi key.
BOOM_ENTRY_EFI = "BOOM_ENTRY_EFI"
#: The ``BootEntry`` options key.
BOOM_ENTRY_OPTIONS = "BOOM_ENTRY_OPTIONS"
#: The ``BootEntry`` device tree key.
BOOM_ENTRY_DEVICETREE = "BOOM_ENTRY_DEVICETREE"
#: The ``BootEntry`` boot identifier key.
BOOM_ENTRY_BOOT_ID = "BOOM_ENTRY_BOOT_ID"

#: An ordered list of all possible ``BootEntry`` keys.
ENTRY_KEYS = [
    # We require a title for each entry (BLS does not)
    BOOM_ENTRY_TITLE,
    # MACHINE_ID is optional in BLS, however, since the standard suggests
    # that it form part of the file name for compliant snippets, it is
    # effectively mandatory.
    BOOM_ENTRY_MACHINE_ID,
    BOOM_ENTRY_VERSION,
    # One of either BOOM_ENTRY_LINUX or BOOM_ENTRY_EFI must be present.
    BOOM_ENTRY_LINUX, BOOM_ENTRY_EFI,
    BOOM_ENTRY_INITRD, BOOM_ENTRY_OPTIONS,
    BOOM_ENTRY_DEVICETREE
]

#: Map Boom entry names to BLS keys
KEY_MAP = {
    BOOM_ENTRY_TITLE: "title",
    BOOM_ENTRY_VERSION: "version",
    BOOM_ENTRY_MACHINE_ID: "machine_id",
    BOOM_ENTRY_LINUX: "linux",
    BOOM_ENTRY_INITRD: "initrd",
    BOOM_ENTRY_EFI: "efi",
    BOOM_ENTRY_OPTIONS: "options",
    BOOM_ENTRY_DEVICETREE: "devicetree"
}


def __make_map_key(key_map):
    """Compatibility function to generate a reverse dictionary on
        Python 2.6 which does not support dictionary comprehension
        notation.
    """
    map_key = {}
    for k, v in key_map.items():
        map_key[v] = k
    return map_key


#: Map BLS entry keys to Boom names
MAP_KEY = __make_map_key(KEY_MAP)

# Module logging configuration
_log = logging.getLogger(__name__)
_log.set_debug_mask(BOOM_DEBUG_ENTRY)

_log_debug = _log.debug
_log_debug_entry = _log.debug_masked
_log_info = _log.info
_log_warn = _log.warning
_log_error = _log.error

#: The global list of boot entries.
_entries = None

#: Pattern for forming root device paths from LVM2 names.
DEV_PATTERN = "/dev/%s"

def boom_entries_path():
    """Return the path to the boom profiles directory.

        :returns: The boom profiles path.
        :returntype: str
    """
    return path_join(get_boot_path(), ENTRIES_PATH)


#: Private constants for Grub2 integration checks
#: Paths outside /boot are referenced relative to /boot.
__grub_cfg = "grub2/grub.cfg"
__etc_grub_d = "../etc/grub.d"
__boom_grub_d = "42_boom"
__etc_default = "../etc/default"
__boom_defaults = "boom"

def check_bootloader():
    """Check the configuration state of the system bootloader to ensure
        that Boom integration is enabled. Currently only Grub2 with the
        Red Hat BLS patches is supported.
    """
    boot_path = get_boot_path()

    grub_cfg = path_join(boot_path, __grub_cfg)
    if not path_exists(grub_cfg):
        _log_warn("No Grub2 configuration file found")
        return False

    boom_grub_d = path_join(boot_path, __etc_grub_d, __boom_grub_d)
    if not path_exists(boom_grub_d):
        _log_warn("Boom grub2 script missing from '%s'" % __etc_grub_d)
        return False

    defaults_file = path_join(boot_path, __etc_default, __boom_defaults)
    if not path_exists(defaults_file):
        _log_warn("Boom configuration file missing from '%s'" % defaults_file)
        return False

    def is_yes(val):
        return val == "y" or val == "yes"

    submenu_enabled = False
    with open(defaults_file, "r") as dfile:
        for line in dfile:
            (name, value) = parse_name_value(line)
            if name == "BOOM_ENABLE_GRUB" and not is_yes(value):
                _log_warn("Boom grub2 integration is disabled in '%s'" %
                          defaults_file)
            if name == "BOOM_USE_SUBMENU" and is_yes(value):
                _log_info("Boom grub2 submenu support enabled")
                submenu_enabled = True
            if name == "BOOM_SUBMENU_NAME" and submenu_enabled:
                _log_info("Boom grub2 submenu name is '%s'" % value)

    found_boom_grub = False
    found_bls = False
    blscfg = "blscfg"
    with open(grub_cfg) as gfile:
        for line in gfile:
            words = line.split()
            if blscfg in line:
                _log_info("Found BLS import statement in '%s'" % grub_cfg)
                found_bls = True
            if "BEGIN" in line and __boom_grub_d in line:
                _log_info("Found Boom Grub2 integration in '%s'" % grub_cfg)
                found_boom_grub = True

    return found_boom_grub or found_bls


class BoomRootDeviceError(BoomError):
    """Boom exception indicating an invalid root device.
    """
    pass


def check_root_device(dev):
    """Test for the presence of root device ``dev`` and return if it
        exists in the configured /dev directory and is a valid block
        device, or raise ``BoomRootDeviceError`` otherwise.

        The exception string indicates the class of error: missing
        path or not a block device.

        :param dev: the root device to check for.
        :raises: BoomRootDeviceError if ``dev`` is invalid.
        :returns: None
    """
    if not path_exists(dev):
        raise BoomRootDeviceError("Device '%s' not found." % dev)

    st = stat(dev)
    if not S_ISBLK(st.st_mode):
        raise BoomRootDeviceError("Path '%s' is not a block device." % dev)


class BootParams(object):
    """The ``BootParams`` class encapsulates the information needed to
        boot an instance of the operating system: the kernel version,
        root device, and root device options.

        A ``BootParams`` object is used to configure a ``BootEntry``
        and to generate configuration keys for the entry based on an
        attached OsProfile.
    """
    #: The kernel version of the instance.
    _version = None

    #: The path to the root device
    _root_device = None

    #: The LVM2 logical volume containing the root file system
    _lvm_root_lv = None

    #: The BTRFS subvolume path to be used as the root file system.
    _btrfs_subvol_path = None

    #: The ID of the BTRFS subvolume to be used as the root file system.
    _btrfs_subvol_id = None

    #: A list of additional kernel options to append
    _add_opts = []

    #: A list of kernel options to drop
    _del_opts = []

    #: Generation counter for dirty detection
    generation = 0

    def __str(self, quote=False, prefix="", suffix=""):
        """Format BootParams as a string.

            Format this ``BootParams`` object as a string, with optional
            prefix, suffix, and value quoting.

            :param quote: A bool indicating whether to quote values.
            :param prefix: An optional prefix string to be concatenated
                           with the start of the formatted string.
            :param suffix: An optional suffix string to be concatenated
                           with the end of the formatted string.
            :returns: a formatted representation of this ``BootParams``.
            :returntype: string
        """
        bp_str = prefix

        fields = ["version", "root_device", "lvm_root_lv",
                  "btrfs_subvol_path", "btrfs_subvol_id"]
        params = (
            self.root_device,
            self.lvm_root_lv,
            self.btrfs_subvol_path, self.btrfs_subvol_id
        )

        # arg
        bp_str += self.version if not quote else '"%s"' % self.version
        bp_str += ", "

        # kwargs

        bp_fmt = "%s=%s, " if not quote else '%s="%s", '
        for fv in [fv for fv in zip(fields[1:], params) if fv[1]]:
            bp_str += bp_fmt % fv

        return bp_str.rstrip(", ") + suffix

    def __str__(self):
        """Format BootParams as a human-readable string.

            Format this ``BootParams`` object as a human-readable string.

            :returns: A human readable string representation of this
                      ``BootParams`` object.

            :returntype: string
        """
        return self.__str()

    def __repr__(self):
        """Format BootParams as a machine-readable string.

            Format this ``BootParams`` object as a machine-readable
            string. The string returned is in the form of a call to the
            ``BootParams`` constructor.

            :returns: a machine readable string represenatation of this
                      ``BootParams`` object.
        """
        return self.__str(quote=True, prefix="BootParams(", suffix=")")

    def __init__(self, version, root_device=None, lvm_root_lv=None,
                 btrfs_subvol_path=None, btrfs_subvol_id=None,
                 add_opts=None, del_opts=None):
        """Initialise a new ``BootParams`` object.

            The root device is specified via the ``root_device``
            argument as a path relative to the root file system.

            The LVM2 logical volume containing the root file system is
            specified using ``lvm_root_lv`` if LVM2 is used.

            For instances using LVM2, if the ``lvm_root_lv`` argument is
            set and ``root_device`` is unset, ``root_device`` is assumed
            to be the normal path of the logical volume specified by the
            ``lvm_root_lv`` argument.

            For instances using BTRFS, the ``root_device`` argument is
            always required.

            Instances using BTRFS may select a subvolume to be mounted
            as the root file system by specifying either the subvolume
            path or id via ``btrfs_subvol_path`` and
            ``btrfs_subvol_id``.

            ``BootParams()`` raises ValueError if a required argument is
            missing, or if conflicting arguments are present.

            :param version: The version string for this BootParams
                            object.
            :param root_device: The root device for this BootParams
                                object.
            :param lvm_root_lv: The LVM2 logical volume containing the
                                root file system, for systems that use
                                LVM.
            :param btrfs_subvol_path: The BTRFS subvolume path
                                      containing the root file system,
                                      for systems using BTRFS.
            :param btrfs_subvol_id: The BTRFS subvolume ID containing
                                    the root file system, for systems
                                    using BTRFS.
            :param add_opts: A list containing additional kernel
                             options to be appended to the command line.
            :param del_opts: A list containing kernel options to be
                             dropped from the command line.
            :returns: a newly initialised BootParams object.
            :returntype: class BootParams
            :raises: ValueError
        """
        if not version:
            raise ValueError("version argument is required.")

        self.version = version

        if root_device:
            self.root_device = root_device

        if lvm_root_lv:
            if not root_device:
                self.root_device = DEV_PATTERN % lvm_root_lv
            self.lvm_root_lv = lvm_root_lv

        if btrfs_subvol_path and btrfs_subvol_id:
            raise ValueError("Only one of btrfs_subvol_path and "
                             "btrfs_subvol_id allowed.")

        if btrfs_subvol_path:
            self.btrfs_subvol_path = btrfs_subvol_path
        if btrfs_subvol_id:
            self.btrfs_subvol_id = btrfs_subvol_id

        self.add_opts = add_opts or []
        self.del_opts = del_opts or []

        _log_debug_entry("Initialised %s" % repr(self))

    # We have to use explicit properties for BootParam attributes since
    # we need to track modifications to the BootParams values to allow
    # a containing BootEntry to mark itself as dirty.

    @property
    def version(self):
        """Return this ``BootParams`` object's version.
        """
        return self._version

    @version.setter
    def version(self, value):
        """Set this ``BootParams`` object's version.
        """
        self.generation += 1
        self._version = value

    @property
    def root_device(self):
        """Return this ``BootParams`` object's root_device.
        """
        return self._root_device

    @root_device.setter
    def root_device(self, value):
        """Set this ``BootParams`` object's root_device.
        """
        self.generation += 1
        self._root_device = value

    @property
    def lvm_root_lv(self):
        """Return this ``BootParams`` object's lvm_root_lv.
        """
        return self._lvm_root_lv

    @lvm_root_lv.setter
    def lvm_root_lv(self, value):
        """Set this ``BootParams`` object's lvm_root_lv.
        """
        self.generation += 1
        self._lvm_root_lv = value

    @property
    def btrfs_subvol_path(self):
        """Return this ``BootParams`` object's btrfs_subvol_path.
        """
        return self._btrfs_subvol_path

    @btrfs_subvol_path.setter
    def btrfs_subvol_path(self, value):
        """Set this ``BootParams`` object's btrfs_subvol_path.
        """
        self.generation += 1
        self._btrfs_subvol_path = value

    @property
    def btrfs_subvol_id(self):
        """Return this ``BootParams`` object's btrfs_subvol_id.
        """
        return self._btrfs_subvol_id

    @btrfs_subvol_id.setter
    def btrfs_subvol_id(self, value):
        """Set this ``BootParams`` object's btrfs_subvol_id.
        """
        self.generation += 1
        self._btrfs_subvol_id = value

    @property
    def add_opts(self):
        """Return this ``BootParams`` object's add_opts.
        """
        return self._add_opts

    @add_opts.setter
    def add_opts(self, value):
        """Set this ``BootParams`` object's add_opts.
        """
        self.generation += 1
        self._add_opts = value

    @property
    def del_opts(self):
        """Return this ``BootParams`` object's del_opts.
        """
        return self._del_opts

    @del_opts.setter
    def del_opts(self, value):
        """Set this ``BootParams`` object's del_opts.
        """
        self.generation += 1
        self._del_opts = value

    def has_btrfs(self):
        """Return ``True`` if this BootParams object is configured to
            use BTRFS.

            :returns: True if BTRFS is in use, or False otherwise
            :returntype: bool
        """
        return any((self.btrfs_subvol_id, self.btrfs_subvol_path))

    def has_lvm2(self):
        """Return ``True`` if this BootParams object is configured to
            use LVM2.

            :returns: True if LVM2 is in use, or False otherwise
            :returntype: bool
        """
        return self.lvm_root_lv is not None and len(self.lvm_root_lv)

    @classmethod
    def from_entry(cls, be):
        """Recover BootParams from BootEntry.

        Recover BootParams values from a templated BootEntry: each
        key subject to template substitution is transformed into a
        regular expression, matching the element and capturing the
        corresponding BootParams value.

        A BootEntry object that has no attached OsProfile cannot be
        reversed since no templates exist to match the entry against:
        in this case None is returned but no exception is raised.
        The entry may be modified and re-written, but no templating
        is possible unless a new, valid, OsProfile is attached.

        :param be: The BootEntry to recover BootParams from.
        :returns: A newly initialised BootParams object.
        :returntype: ``BootParams``
        :raises: ValueError if expected values cannot be matched.
        """
        osp = be._osp
        # Version is written directly from BootParams
        version = be.version
        bp = BootParams(version)
        matches = {}

        _log_debug_entry("Initialising BootParams() from "
                         "BootEntry(boot_id='%s')" % be.boot_id)

        opts_regexes = osp.make_format_regexes(osp.options)
        if not opts_regexes:
            return None

        _log_debug_entry("Matching options regex list with %d entries" %
                         len(opts_regexes))
        _log_debug_entry("Options regex list: %s" % str(opts_regexes))

        opts_matched = []
        for rgx_word in opts_regexes:
            (name, exp) = rgx_word
            value = ""
            for word in be.options.split():
                match = re.search(exp, word) if name else re.match(exp, word)
                if match:
                    matches[word] = True
                    if len(match.groups()):
                        value = match.group(1)
                        _log_debug_entry("Matched: '%s' (%s)" %
                                         (value, name))
                    setattr(bp, name, value)
                    continue

            # The root_device key is handled specially since it is required
            # for a valid BootEntry.
            if name == 'root_device' and not value:
                _log_warn("Entry with boot_id=%s has no root_device"
                          % be.boot_id)
                setattr(bp, name, "")

        def is_add(opt):
            """Return ``True`` if ``opt`` was appended to this options line,
                and was not generated from an ``OsProfile`` template.
            """
            return opt not in matches.keys()

        def is_del(opt):
            """Return ``True`` if the option regex `opt` has been deleted
                from this options line. An option is dropped if it is in
                the ``OsProfile`` template and is absent from the option
                line.

                Optional boot parameters (e.g. rd.lvm.lv and rootflags)
                are ignored since these are only templated when the
                corresponding boot parameter is set.

                The fact that an option is dropped is recorded for later
                templating operations.
            """
            # Ignore optional boot parameters
            ignore_bp = ['rootflags', 'rd.lvm.lv', 'subvol', 'subvolid']
            opt_name = opt.split('=')[0]
            matched_opts = [k.split('=')[0] for k in matches.keys()]
            if opt_name not in matched_opts and opt_name not in ignore_bp:
                return True
            return False

        # Compile list of unique non-template options
        bp.add_opts = [opt for opt in be.options.split() if is_add(opt)]
        bp.add_opts = list(set(bp.add_opts))

        # Compile list of deleted template options
        bp.del_opts = [o for o in [r[1] for r in opts_regexes] if is_del(o)]

        _log_debug_entry("Parsed %s" % repr(bp))

        return bp

def _add_entry(entry):
    """Add a new entry to the list of loaded on-disk entries.

        :param entry: The ``BootEntry`` to add.
    """
    global _entries
    if _entries is None:
        load_entries()
    if entry not in _entries:
        _entries.append(entry)


def _del_entry(entry):
    """Remove a ``BootEntry`` from the list of loaded entries.

        :param entry: The ``BootEntry`` to remove.
    """
    global _entries
    _entries.remove(entry)


def drop_entries():
    """Drop all in-memory entries.

        Clear the list of in-memory entries and reset the BootEntry
        list to the default state.

        :returns: None
    """
    global _entries
    _entries = []


def load_entries(machine_id=None):
    """ Load boot entries into memory.

        Load boot entries from ``boom.bootloader.boom_entries_path()``.

        If ``machine_id`` is specified only matching entries will be
        considered.

        :param machine_id: A ``machine_id`` value to match.
    """
    global _entries
    if not profiles_loaded():
        load_profiles()

    entries_path = boom_entries_path()

    drop_entries()

    _log_info("Loading boot entries from '%s'" % entries_path)
    for entry in listdir(entries_path):
        if not entry.endswith(".conf"):
            continue
        if machine_id and machine_id not in entry:
            _log_debug_entry("Skipping entry with machine_id!='%s'",
                             machine_id)
            continue
        entry_path = path_join(entries_path, entry)
        try:
            _add_entry(BootEntry(entry_file=entry_path))
        except Exception as e:
            _log_info("Could not load BootEntry '%s': %s" %
                      (entry_path, e))

    _log_info("Loaded %d entries" % len(_entries))


def write_entries():
    """Write out boot entries.

        Write all currently loaded boot entries to
        ``boom.bootloader.boom_entries_path()``.
    """
    global _entries
    for be in _entries:
        try:
            be.write_entry()
        except Exception as e:
            _log_warn("Could not write BootEntry(boot_id='%s'): %s" %
                      (be.disp_boot_id, e))


def min_boot_id_width():
    """Calculate the minimum unique width for boot_id values.

        Calculate the minimum width to ensure uniqueness when displaying
        boot_id values.

        :returns: the minimum boot_id width.
        :returntype: int
    """
    return min_id_width(7, _entries, "boot_id")

def select_params(s, bp):
    """Test BootParams against Selection criteria.

        Test the supplied ``BootParams`` against the selection criteria
        in ``s`` and return ``True`` if it passes, or ``False``
        otherwise.

        :param bp: The BootParams to test
        :returntype: bool
        :returns: True if BootParams passes selection or ``False``
                  otherwise.
    """
    if s.root_device and s.root_device != bp.root_device:
        return False
    if s.lvm_root_lv and s.lvm_root_lv != bp.lvm_root_lv:
        return False
    if s.btrfs_subvol_path and s.btrfs_subvol_path != bp.btrfs_subvol_path:
        return False
    if s.btrfs_subvol_id and s.btrfs_subvol_id != bp.btrfs_subvol_id:
        return False

    return True

def select_entry(s, be):
    """Test BootEntry against Selection criteria.

        Test the supplied ``BootEntry`` against the selection criteria
        in ``s`` and return ``True`` if it passes, or ``False``
        otherwise.

        :param bp: The BootEntry to test
        :returntype: bool
        :returns: True if BootEntry passes selection or ``False``
                  otherwise.
    """
    if not select_profile(s, be._osp):
        return False

    if s.boot_id and not be.boot_id.startswith(s.boot_id):
        return False
    if s.title and be.title != s.title:
        return False
    if s.version and be.version != s.version:
        return False
    if s.machine_id and be.machine_id != s.machine_id:
        return False

    if not select_params(s, be.bp):
        return False

    return True


def find_entries(selection=None):
    """Find boot entries matching selection criteria.

        Return a list of ``BootEntry`` objects matching the specified
        criteria. Matching proceeds as the logical 'and' of all criteria.
        Criteria that are unset (``None``) are ignored.

        If no ``BootEntry`` matches the specified criteria the empty list
        is returned.

        Boot entries will be automatically loaded from disk if they are
        not already in memory.

        :param selection: A ``Selection`` object specifying the match
                          criteria for the operation.
        :returns: a list of ``BootEntry`` objects.
        :returntype: list
    """
    global _entries

    if not _entries:
        load_entries()

    matches = []

    # Use null search criteria if unspecified
    selection = selection if selection else Selection()

    selection.check_valid_selection(entry=True, params=True, profile=True)

    _log_debug_entry("Finding entries for %s" % repr(selection))

    for be in _entries:
        if select_entry(selection, be):
            matches.append(be)
    _log_debug_entry("Found %d entries" % len(matches))
    return matches


def _transform_key(key_name):
    """Transform key characters between Boom and BLS notation.

        Transform all occurrences of '_' in ``key_name`` to '-' or vice
        versa.

        Key names on-disk use a hyphen as the word separator, for e.g.
        "machine-id". We cannot use this character for Python attributes
        since it collides with the subtraction operator.

        :param key_name: The key name to be transformed.

        :returns: The transformed key name.

        :returntype: string
    """
    if "_" in key_name:
        return key_name.replace("_", "-")
    if "-" in key_name:
        return key_name.replace("-", "_")
    return key_name


class BootEntry(object):
    """A class representing a BLS compliant boot entry.

        A ``BootEntry`` exposes two sets of properties that are the
        keys of a BootLoader Specification boot entry.

        The properties of a ``BootEntry`` that is not associated with an
        ``OsProfile`` (for e.g. one read from disk) are the literal
        values read from a file or set through the API.

        When an ``OSProfile`` is attached to a ``BootEntry``, it is used
        as a template to fill out the values of keys for properties
        including the kernel and initramfs file name. This is used to
        create new ``BootEntry`` objects to be written to disk.

        An ``OsProfile`` can be attached to a ``BootEntry`` when it is
        created, or at a later time by calling the ``set_os_profile()``
        method.
    """
    _entry_data = None
    _unwritten = False
    _last_path = None
    _comments = None
    _osp = None
    _bp = None
    _bp_generation = None

    # boot_id cache
    __boot_id = None

    def __str(self, quote=False, prefix="", suffix="", tail="\n",
              sep=" ", bls=True, no_boot_id=False):
        """Format BootEntry as a string.

            Return a human or machine readable representation of this
            BootEntry.

            :param quote: True if values should be quoted or False otherwise.

            :param prefix:An optional prefix string to be concatenated with
                          with the start of the formatted string.

            :param suffix: An optional suffix string to be concatenated
                           with the end of the formatted string.

            :param tail: A string to be concatenated between subsequent
                         records in the formatted string.

            :param sep: A separator to be inserted between each name and
                        value. Normally either ' ' or '='.

            :param bls: Generate output using BootLoader Specification
                        syntax and key names.

            :param no_boot_id: Do not include the BOOM_ENTRY_BOOT_ID key in the
                               returned string. Used internally in
                               order to avoid recursion when calculating
                               the BOOM_ENTRY_BOOT_ID checksum.

            :returns: A string representation.

            :returntype: string
        """
        be_str = prefix

        for key in [k for k in ENTRY_KEYS if getattr(self, KEY_MAP[k])]:
            attr = KEY_MAP[key]
            key_fmt = '%s%s"%s"' if quote else '%s%s%s'
            key_fmt += tail
            attr_val = getattr(self, attr)
            if bls:
                key_data = (_transform_key(attr), sep, attr_val)
            else:
                key_data = (key, sep, attr_val)
            be_str += key_fmt % key_data

        # BOOM_ENTRY_BOOT_ID requires special handling to avoid
        # recursion from the boot_id property method (which uses the
        # string representation of the object to calculate the
        # checksum).
        if not bls and not no_boot_id:
            key_fmt = ('%s%s"%s"' if quote else '%s%s%s') + tail
            boot_id_data = [BOOM_ENTRY_BOOT_ID, sep, self.boot_id]
            be_str += key_fmt % tuple(boot_id_data)

        return be_str.rstrip(tail) + suffix

    def __str__(self):
        """Format BootEntry as a human-readable string in BLS notation.

            Format this BootEntry as a string containing a BLS
            configuration snippet.

            :returns: a BLS configuration snippet corresponding to this entry.

            :returntype: string
        """
        return self.__str()

    def __repr__(self):
        """Format BootEntry as a machine-readable string.

            Return a machine readable representation of this BootEntry,
            in constructor notation.

            :returns: A string in BootEntry constructor syntax.

            :returntype: str
        """
        return self.__str(quote=True, prefix="BootEntry(entry_data={",
                          suffix="})", tail=", ", sep=": ", bls=False)

    def __len__(self):
        """Return the length (key count) of this ``BootEntry``.

            :returns: the ``BootEntry`` length as an integer.
            :returntype: ``int``
        """
        return len(self._entry_data)

    def __eq__(self, other):
        """Test for equality between this ``BootEntry`` and another
            object.

            Equality for ``BootEntry`` objects is true if the both
            ``boot_id`` values match.

            :param other: The object against which to test.

            :returns: ``True`` if the objects are equal and ``False``
                      otherwise.
            :returntype: bool
        """
        if not hasattr(other, "boot_id"):
            return False
        if self.boot_id == other.boot_id:
            return True
        return False

    def __getitem__(self, key):
        """Return an item from this ``BootEntry``.

            :returns: the item corresponding to the key requested.
            :returntype: the corresponding type of the requested key.
            :raises: TypeError if ``key`` is of an invalid type.
                     KeyError if ``key`` is valid but not present.
        """
        if not isinstance(key, str):
            raise TypeError("BootEntry key must be a string.")

        if key in self._entry_data:
            return self._entry_data[key]
        if key == BOOM_ENTRY_LINUX:
            return self.linux
        if key == BOOM_ENTRY_INITRD:
            return self.initrd
        if key == BOOM_ENTRY_OPTIONS:
            return self.options
        if key == BOOM_ENTRY_DEVICETREE:
            return self.devicetree
        if key == BOOM_ENTRY_EFI:
            return self.efi
        if key == BOOM_ENTRY_BOOT_ID:
            return self.boot_id
        if self.bp and key == BOOM_ENTRY_VERSION:
            return self.bp.version

        raise KeyError("BootEntry key %s not present." % key)

    def __setitem__(self, key, value):
        """Set the specified ``BootEntry`` key to the given value.

            :param key: the ``BootEntry`` key to be set.
            :param value: the value to set for the specified key.
        """
        if not isinstance(key, str):
            raise TypeError("BootEntry key must be a string.")

        if key == BOOM_ENTRY_VERSION and self.bp:
            self.bp.version = value
        elif key == BOOM_ENTRY_LINUX and self.bp:
            self.linux = value
        elif key == BOOM_ENTRY_INITRD and self.bp:
            self.initrd = value
        elif key == BOOM_ENTRY_OPTIONS and self.bp:
            self.options = value
        elif key == BOOM_ENTRY_DEVICETREE and self.bp:
            self.devicetree = value
        elif key == BOOM_ENTRY_EFI and self.bp:
            self.efi = value
        elif key == BOOM_ENTRY_BOOT_ID:
            raise TypeError("'boot_id' property does not support assignment")
        elif key in self._entry_data:
            self._entry_data[key] = value
        else:
            raise KeyError("BootEntry key %s not present." % key)

    def keys(self):
        """Return the list of keys for this ``BootEntry``.

            Return a copy of this ``BootEntry``'s keys as a list of
            key name strings.

            :returns: the current list of ``BotoEntry`` keys.
            :returntype: list of str
        """
        keys = list(self._entry_data.keys())
        add_keys = [BOOM_ENTRY_LINUX, BOOM_ENTRY_INITRD, BOOM_ENTRY_OPTIONS]

        # Sort the item list to give stable list ordering on Py3.
        keys = sorted(keys, reverse=True)

        if self.bp:
            add_keys.append(BOOM_ENTRY_VERSION)

        for k in add_keys:
            if k not in self._entry_data:
                keys.append(k)

        return keys

    def values(self):
        """Return the list of values for this ``BootEntry``.

            Return a copy of this ``BootEntry``'s values as a list.

            :returns: the current list of ``BotoEntry`` values.
            :returntype: list
        """
        values = list(self._entry_data.values())
        add_values = [self.linux, self.initrd, self.options]

        # Sort the item list to give stable list ordering on Py3.
        values = sorted(values, reverse=True)

        if self.bp:
            add_values.append(self.version)

        return values + add_values

    def items(self):
        """Return the items list for this BootEntry.

            Return a copy of this ``BootEntry``'s ``(key, value)``
            pairs as a list.

            :returns: the current list of ``BotoEntry`` items.
            :returntype: list of ``(key, value)`` tuples.
        """
        items = list(self._entry_data.items())

        add_items = [
            (BOOM_ENTRY_LINUX, self.linux),
            (BOOM_ENTRY_INITRD, self.initrd),
            (BOOM_ENTRY_OPTIONS, self.options)
        ]

        if self.bp:
            add_items.append((BOOM_ENTRY_VERSION, self.version))

        # Sort the item list to give stable list ordering on Py3.
        items = sorted(items, key=lambda i:i[0], reverse=True)

        return items + add_items

    def _dirty(self):
        """Mark this ``BootEntry`` as needing to be written to disk.

            A newly created ``BootEntry`` object is always dirty and
            a call to its ``write_entry()`` method will always write
            a new boot entry file. Writes may be avoided for entries
            that are not marked as dirty.

            A clean ``BootEntry`` is marked as dirty if a new value
            is written to any of its writable properties.

            :returntype: None
        """
        # Clear cached boot_id: it will be regenerated on next access
        self.__boot_id = None
        self._unwritten = True

    def __os_id_from_comment(self, comment):
        """Retrive OsProfile from BootEntry comment.

            Attempt to set this BootEntry's OsProfile using a comment
            string stored in the entry file. The comment must be of the
            form "OsIdentifier: <os_id>". If found the value is treated
            as authoritative and a reference to the corresponding
            ``OsProfile`` is stored  in the object's ``_osp`` member.

            Any comment lines that do not contain an OsIdentifier tag
            are returned as a multi-line string.

            :param comment: The comment to attempt to parse
            :returns: Comment lines not containing an OsIdentifier
            :returntype: str
        """
        if "OsIdentifier:" not in comment:
            return

        outlines = ""
        for line in comment.splitlines():
            (key, os_id) = line.split(":")
            os_id = os_id.strip()
            osp = get_os_profile_by_id(os_id)

            # An OsIdentifier comment is automatically added to the
            # entry when it is written: do not add the read value to
            # the comment list.
            if not self._osp and osp:
                self._osp = osp
                _log_debug_entry("Parsed os_id='%s' from comment" %
                                 osp.disp_os_id)
            else:
                outlines += line + "\n"
        return outlines

    def __match_os_profile(self):
        """Attempt to find a matching OsProfile for this BootEntry.

            Attempt to guess the correct ``OsProfile`` to use with
            this ``BootEntry`` by probing each loaded ``OsProfile``
            in turn until a profile recognises the entry. If no match
            is found the entrie's ``OsProfile`` is set to ``None``.

            Probing is only used in the case that a loaded entry has
            no embedded OsIdentifier string. All entries written by
            Boom include the OsIdentifier value: probing is primarily
            useful for entries that have been manually written or
            edited.
        """
        self._osp = match_os_profile(self)

    def __match_host_profile(self):
        """Attempt to find a matching HostProfile for this BootEntry.

            Try to find a ``HostProfile`` with a matching machine_id,
            and if one is found, wrap this ``BootEntry``'s operating
            system profile with the host.

            This method must be called with a valid ``BootParams``
            object attached.
        """
        if BOOM_ENTRY_MACHINE_ID in self._entry_data:
            machine_id = self._entry_data[BOOM_ENTRY_MACHINE_ID]
            hps = find_host_profiles(Selection(machine_id=machine_id))
            self._osp = hps[0] if hps else self._osp

        # Import add/del options from HostProfile if attached.
        if hasattr(self._osp, "add_opts"):
            self.bp.add_opts = self._osp.add_opts.split()

        if hasattr(self._osp, "del_opts"):
            self.bp.del_opts = self._osp.del_opts.split()

    def __from_data(self, entry_data, boot_params):
        """Initialise a new BootEntry from in-memory data.

            Initialise a new ``BootEntry`` object with data from the
            dictionary ``entry_data`` (and optionally the supplied
            ``BootParams`` object). The supplied dictionary should be
            indexed by Boom entry key names (``BOOM_ENTRY_*``).

            Raises ``ValueError`` if required keys are missing
            (``BOOM_ENTRY_TITLE``, and either ``BOOM_ENTRY_LINUX`` or
            ``BOOM_ENTRY_EFI``).

            This method should not be called directly: to build a new
            ``BootEntry`` object from in-memory data, use the class
            initialiser with the ``entry_data`` argument.

            :param entry_data: A dictionary mapping Boom boot entry key
                               names to values
            :param boot_params: Optional BootParams to attach to the new
                                BootEntry object
            :returns: None
            :returntype: None
            :raises: ValueError
        """
        if BOOM_ENTRY_TITLE not in entry_data:
            raise ValueError("BootEntry missing BOOM_ENTRY_TITLE")

        if BOOM_ENTRY_LINUX not in entry_data:
            if BOOM_ENTRY_EFI not in entry_data:
                raise ValueError("BootEntry missing BOOM_ENTRY_LINUX or"
                                 " BOOM_ENTRY_EFI")

        self._entry_data = {}
        for key in [k for k in ENTRY_KEYS if k in entry_data]:
            self._entry_data[key] = entry_data[key]

        if not self._osp:
            self.__match_os_profile()

        if boot_params:
            self.bp = boot_params
            # boot_params is always authoritative
            self._entry_data[BOOM_ENTRY_VERSION] = self.bp.version
        else:
            # Attempt to recover BootParams from entry data
            self.bp = BootParams.from_entry(self)

        # Wrap OsProfile in HostProfile if available
        self.__match_host_profile()

        if self.bp:
            def _pop_if_set(key):
                if key in _entry_data:
                    if _entry_data[key] == getattr(self, KEY_MAP[key]):
                        _entry_data.pop(key)

            # Copy the current _entry_data and clear self._entry_data to
            # allow comparison of stored value with template.
            _entry_data = self._entry_data
            self._entry_data = {}

            # Clear templated keys from _entry_data and if the value
            # read from entry_data is identical to that generated by the
            # current OsProfile and BootParams.
            _pop_if_set(BOOM_ENTRY_VERSION)
            _pop_if_set(BOOM_ENTRY_LINUX)
            _pop_if_set(BOOM_ENTRY_INITRD)
            _pop_if_set(BOOM_ENTRY_OPTIONS)
            self._entry_data = _entry_data

    def __from_file(self, entry_file, boot_params):
        """Initialise a new BootEntry from on-disk data.

            Initialise a new ``BootEntry`` using the entry data in
            ``entry_file`` (and optionally the supplied ``BootParams``
            object).

            Raises ``ValueError`` if required keys are missing
            (``BOOM_ENTRY_TITLE``, and either ``BOOM_ENTRY_LINUX`` or
            ``BOOM_ENTRY_EFI``).

            This method should not be called directly: to build a new
            ``BootEntry`` object from entry file data, use the class
            initialiser with the ``entry_file`` argument.

            :param entry_file: The path to a file containing a BLS boot
                               entry
            :param boot_params: Optional BootParams to attach to the new
                                BootEntry object
            :returns: None
            :returntype: None
            :raises: ValueError
        """
        entry_data = {}
        comments = {}
        comment = ""

        entry_basename = basename(entry_file)
        _log_debug("Loading BootEntry from '%s'" % entry_basename)

        with open(entry_file, "r") as ef:
            for line in ef:
                if blank_or_comment(line):
                    comment += line if line else ""
                else:
                    bls_key, value = parse_name_value(line, separator=None)
                    # Convert BLS key name to Boom notation
                    key = _transform_key(bls_key)
                    if key not in MAP_KEY:
                        raise LookupError("Unknown BLS key '%s'" % bls_key)
                    key = MAP_KEY[_transform_key(bls_key)]
                    entry_data[key] = value
                    if comment:
                        comment = self.__os_id_from_comment(comment)
                        if not comment:
                            continue
                        comments[key] = comment
                        comment = ""
        self._comments = comments

        self.__from_data(entry_data, boot_params)

        match = re.match(BOOT_ENTRIES_PATTERN, entry_basename)
        if not match or len(match.groups()) <= 1:
            _log_warn("Unknown boot entry file: %s" % entry_basename)
        else:
            if self.disp_boot_id != match.group(2):
                _log_info("Entry file name does not match boot_id: %s" %
                          entry_basename)

        self._last_path = entry_file
        self._unwritten = False

    def __init__(self, title=None, machine_id=None, osprofile=None,
                 boot_params=None, entry_file=None, entry_data=None,
                 allow_no_dev=False):
        """Initialise new BootEntry.

            Initialise a new ``BootEntry`` object from the specified
            file or using the supplied values.

            If ``osprofile`` is specified the profile is attached to the
            new ``BootEntry`` and will be used to supply templates for
            ``BootEntry`` values.

            A ``BootParams`` object may be supplied using the
            ``boot_params`` keyword argument. The object will be used to
            provide values for subsitution using the patterns defined by
            the configured ``OsProfile``.

            If ``entry_file`` is specified the ``BootEntry`` will be
            initialised from the values found in the file, which should
            contain a valid BLS snippet in UTF-8 encoding. The file may
            contain blank lines and comments (lines beginning with '#'),
            and these will be preserved if the entry is re-written.

            If ``entry_file`` is not specified, both ``title`` and
            ``machine_id`` must be given.

            The ``entry_data`` keyword argument is an optional argument
            used to initialise a ``BootEntry`` from a dictionary mapping
            ``BOOM_ENTRY_*`` keys to ``BootEntry`` values. It may be used to
            initialised a new ``BootEntry`` using the strings obtained
            from a call to ``BootEntry.__repr__()``.

            :param title: The title for this ``BootEntry``.

            :param machine_id: The ``machine_id`` of this ``BootEntry``.

            :param osprofile: An optional ``OsProfile`` to attach to
                              this ``BootEntry``.

            :param boot_params: An optional ``BootParams`` object to
                                initialise this ``BooyEntry``.

            :param entry_file: An optional path to a file in the file
                               system containing a boot entry in BLS
                               notation.

            :param entry_data: An optional dictionary of ``BootEntry``
                               key to value mappings to initialise
                               this ``BootEntry`` from.

            :returns: A new ``BootEntry`` object.

            :returntype: BootEntry
        """
        # An osprofile kwarg always takes precedent over either an
        # 'OsIdentifier' comment or a matched osprofile value.
        self._osp = osprofile

        if entry_data:
            return self.__from_data(entry_data, boot_params)
        if entry_file:
            return self.__from_file(entry_file, boot_params)

        self._unwritten = True

        if not machine_id:
            raise ValueError("BootEntry machine_id cannot be None")

        self.bp = boot_params

        # The BootEntry._entry_data dictionary contains data for an existing
        # BootEntry that has been read from disk, as well as any overridden
        # fields for a new BootEntry with an OsProfile attached.
        self._entry_data = {}

        def title_empty(osp, title):
            if self._osp and not self._osp.title:
                return True
            elif not self._osp and not title:
                return True
            return False

        if title:
            self.title = title
        elif title_empty(self._osp, title):
            raise ValueError("BootEntry title cannot be empty")

        self.machine_id = machine_id

        if not self._osp:
            self.__match_os_profile()

        # Wrap OsProfile in HostProfile if available
        self.__match_host_profile()

        if self.bp:
            if not allow_no_dev:
                check_root_device(self.bp.root_device)

    def _apply_format(self, fmt):
        """Apply key format string substitution.

            Apply format key substitution to format string ``fmt``,
            using values provided by an attached ``BootParams`` object,
            and string patterns from either an associated ``OsProfile``
            object, or values set directly in this ``BootEntry``.

            If the source of data for a key is empty or None, the
            string is returned unchanged.

            The currently defined format keys are:

            * ``%{version}`` The kernel version string.
            * ``%{lvm_root_lv}`` The LVM2 logical volume containing the
              root file system.
            * ``%{btrfs_subvolume}`` The root flags specifying the BTRFS
              subvolume containing the root file system.
            * ``%{root_device}`` The device containing the root file
              system.
            * ``%{root_opts}`` The command line options required for the
              root file system.
            * ``%{linux}`` The linux image to boot
            * ``%{os_name}`` The OS Profile name
            * ``%{os_short_name`` The OS Profile short name
            * ``%{os_version}`` The OS Profile version
            * ``%{os_version id`` The OS Profile version ID

            :param fmt: The string to be formatted.

            :returns: The formatted string
            :returntype: str
        """
        orig = fmt
        key_format = "%%{%s}"
        bp = self.bp

        if not fmt:
            return ""

        # Table-driven key formatting
        #
        # Each entry in the format_key_specs table specifies a list of
        # possible key substitutions to perform for the named key. Each
        # entry of the key_spec list contains a dictionary containing
        # one or more attribute sources or predicates.
        #
        # A key substitution is evaluated if at least one of the listed
        # attribute sources is defined, and if all defined predicates
        # evaluate to True. A predicate must be a Python callable
        # accepting no arguments and returning a boolean. A key_spec
        # may also specify an explicit list of needed objects, "bp",
        # or "osp", that must exist to evaluate predicates.
        #
        # Several helper functions exist to obtain key values from the
        # appropriate data source (accounting for keys that exist in
        # multiple objects as well as keys that return None or empty
        # values), to test key_spec predicates, and to safely obtain
        # function attributes where the containing object may or may
        # not exist.
        def get_key_attr(key_spec):
            """Return a key's value attribute.

                Return a value from either `BootParams`, `OsProfile`,
                or `BootEntry`. Each source is tested in order and the
                value is taken from the first object type with a value
                for the named key.
            """
            def have_attr():
                """Test whether any attribute source for this key exists.
                """
                attrs_vals = [
                    (BP_ATTR, bp), (OSP_ATTR, self._osp), (BE_ATTR, True)
                ]
                have = False
                for attr, source in attrs_vals:
                    if attr in key_spec:
                        have |= source is not None
                return have

            val_fmt = "%s" if VAL_FMT not in key_spec else key_spec[VAL_FMT]

            if have_attr():
                if BP_ATTR in key_spec and bp:
                    value = getattr(bp, key_spec[BP_ATTR])
                elif OSP_ATTR in key_spec:
                    value = getattr(self._osp, key_spec[OSP_ATTR])
                elif BE_ATTR in key_spec:
                    value = getattr(self, key_spec[BE_ATTR])
                return val_fmt % value if value is not None else None
            else:
                return None

        def test_predicates(key_spec):
            """Test all defined predicate functions and return `True` if
                all evaluate `True`, or `False` otherwise.
            """
            needs = key_spec[NEEDS] if NEEDS in key_spec else []
            for need in needs:
                if need == "bp" and not bp:
                    return False
                if need == "osp" and not self._osp:
                    return False
            predicates = key_spec[PRED_FN]
            # Ignore invalid predicates
            return all([fn() for fn in predicates if fn])

        def mkpred(obj, fn):
            """Return a callable predicate function for method ``fn`` of
                object ``obj`` if ``obj`` is valid and contains ``fn``,
                or ``None`` otherwise.

                This is used to safely build predicate function lists
                whether or not the objects they reference are defined
                or not for a given substitution key.
            """
            return getattr(obj, fn) if obj else None

        # Key spec constants
        BE_ATTR = "be_attr"
        BP_ATTR = "bp_attr"
        OSP_ATTR = "osp_attr"
        PRED_FN = "fn_pred"
        VAL_FMT = "val_fmt"
        NEEDS = "needs"

        format_key_specs = {
            FMT_VERSION: [{BE_ATTR:"version", BP_ATTR:"version"}],
            FMT_LVM_ROOT_LV: [{BP_ATTR: "lvm_root_lv"}],
            FMT_LVM_ROOT_OPTS: [{OSP_ATTR: "root_opts_lvm2"}],
            FMT_BTRFS_ROOT_OPTS: [{OSP_ATTR: "root_opts_btrfs"}],
            FMT_BTRFS_SUBVOLUME: [{BP_ATTR:"btrfs_subvol_id", NEEDS: "bp",
                                   PRED_FN: [mkpred(bp, "has_btrfs")],
                                   VAL_FMT: "subvolid=%s"},
                                  {BP_ATTR:"btrfs_subvol_path", NEEDS: "bp",
                                   PRED_FN: [mkpred(bp, "has_btrfs")],
                                   VAL_FMT: "subvol=%s"}],
            FMT_ROOT_DEVICE: [{BP_ATTR: "root_device", NEEDS: "bp"}],
            FMT_ROOT_OPTS: [{BE_ATTR: "root_opts", NEEDS: "bp"}],
            FMT_KERNEL: [{BE_ATTR: "linux", NEEDS: "bp"}],
            FMT_INITRAMFS: [{BE_ATTR: "initrd", NEEDS: "bp"}],
            FMT_OS_NAME: [{OSP_ATTR: "os_name"}],
            FMT_OS_NAME: [{OSP_ATTR: "os_short_name"}],
            FMT_OS_NAME: [{OSP_ATTR: "os_version"}],
            FMT_OS_NAME: [{OSP_ATTR: "os_version_id"}]
        }

        for key_name in format_key_specs.keys():
            key = key_format % key_name
            if not key in fmt:
                continue
            for key_spec in format_key_specs[key_name]:
                # Check NEEDS
                for k in key_spec.keys():
                    if k == NEEDS:
                        if key_spec[k] == "bp" and not bp:
                            continue
                        if key_spec[k] == "osp" and not self._osp:
                            continue
                # A key value of None means the key should not be substituted:
                # this occurs when accessing a templated attribute of an entry
                # that has no attached OsProfile (in which case the format key
                # is retained in the formatted text).
                #
                # If the value is not None, but contains the empty string, the
                # value is substituted as normal.
                value = get_key_attr(key_spec)
                if value is None:
                    continue
                fmt = fmt.replace(key, value)

        return fmt

    def __generate_boot_id(self):
        """Generate a new boot_id value.

            Generate a new sha1 profile identifier for this entry,
            using the title, version, root_device and any defined
            LVM2 or BTRFS snapshot parameters.

            :returns: A ``boot_id`` string
            :returntype: str
        """
        # The default ``str()`` and ``repr()`` behaviour for
        # ``BootEntry`` objects includes the ``boot_id`` value. This
        # must be disabled in order to generate the ``boot_id`` to
        # avoid recursing into __generate_boot_id() from the string
        # formatting methods.
        #
        # Call the underlying ``__str()`` method directly and disable
        # the inclusion of the ``boot_id``.
        #
        # Other callers should always rely on the standard methods.
        boot_id = sha1(self.__str(no_boot_id=True).encode('utf-8')).hexdigest()
        _log_debug_entry("Generated new boot_id='%s'" % boot_id)
        return boot_id

    def _entry_data_property(self, name):
        """Return property value from entry data.

            :param name: The boom key name of the property to return
            :returns: The property value from the entry data dictionary
        """
        if self._entry_data and name in self._entry_data:
            return self._entry_data[name]
        return None

    @property
    def bp(self):
        """The ``BootParams`` object associated with this ``BootEntry``.
        """
        return self._bp

    @bp.setter
    def bp(self, value):
        """Set the ``BootParams`` object associated with this
            ``BootEntry``.
        """
        self._dirty()
        self._bp = value
        self._bp_generation = self._bp.generation if self._bp else 0

    @property
    def disp_boot_id(self):
        """The display boot_id of this entry.

            Return the shortest prefix of this BootEntry's boot_id that
            is unique within the current set of loaded entries.

            :getter: return this BootEntry's boot_id.
            :type: str
        """
        return self.boot_id[:min_boot_id_width()]

    @property
    def boot_id(self):
        """A SHA1 digest that uniquely identifies this ``BootEntry``.

            :getter: return this ``BootEntry``'s ``boot_id``.
            :type: string
        """
        # Mark ourself dirty if boot parameters have changed.
        if self.bp and self.bp.generation != self._bp_generation:
            self._bp_generation = self.bp.generation
            self._dirty()
        if not self.__boot_id or self._unwritten:
            self.__boot_id = self.__generate_boot_id()
        return self.__boot_id

    @property
    def root_opts(self):
        """The root options that should be used for this ``BootEntry``.

            :getter: Returns the root options string for this ``BootEntry``.
            :type: string
        """
        if not self._osp or not self.bp:
            return ""
        bp = self.bp
        osp = self._osp
        root_opts = "%s%s%s"
        lvm_opts = ""
        if bp.lvm_root_lv:
            lvm_opts = self._apply_format(osp.root_opts_lvm2)

        btrfs_opts = ""
        if bp.btrfs_subvol_id or bp.btrfs_subvol_path:
            btrfs_opts += self._apply_format(osp.root_opts_btrfs)
        spacer = " " if lvm_opts and btrfs_opts else ""
        return root_opts % (lvm_opts, spacer, btrfs_opts)

    @property
    def title(self):
        """The title of this ``BootEntry``.

            :getter: returns the ``BootEntry`` title.
            :setter: sets this ``BootEntry`` object's title.
            :type: string
        """
        if BOOM_ENTRY_TITLE in self._entry_data:
            return self._entry_data_property(BOOM_ENTRY_TITLE)

        if not self._osp or not self.bp:
            return ""

        osp = self._osp
        return self._apply_format(osp.title)

    @title.setter
    def title(self, title):
        if not title:
            # It is valid to set an empty title in a HostProfile as long
            # as the OsProfile defines one.
            if not self._osp or not self._osp.title:
                raise ValueError("Entry title cannot be empty")
        self._entry_data[BOOM_ENTRY_TITLE] = title
        self._dirty()

    @property
    def machine_id(self):
        """The machine_id of this ``BootEntry``.

            :getter: returns this ``BootEntry`` object's ``machine_id``.
            :setter: sets this ``BootEntry`` object's ``machine_id``.
            :type: string
        """
        return self._entry_data_property(BOOM_ENTRY_MACHINE_ID)

    @machine_id.setter
    def machine_id(self, machine_id):
        self._entry_data[BOOM_ENTRY_MACHINE_ID] = machine_id
        self._dirty()

    @property
    def version(self):
        """The version string associated with this ``BootEntry``.

            :getter: returns this ``BootEntry`` object's ``version``.
            :setter: sets this ``BootEntry`` object's ``version``.
            :type: string
        """
        if self.bp and BOOM_ENTRY_VERSION not in self._entry_data:
            return self.bp.version
        return self._entry_data_property(BOOM_ENTRY_VERSION)

    @version.setter
    def version(self, version):
        self._entry_data[BOOM_ENTRY_VERSION] = version
        self._dirty()

    @property
    def options(self):
        """The command line options for this ``BootEntry``.

            :getter: returns the command line for this ``BootEntry``.
            :setter: sets the command line for this ``BootEntry``.
            :type: string
        """
        def add_opts(opts, append):
            """Append additional kernel options to this ``BootEntry``'s
                options property.
            """
            extra = " ".join(append)
            return "%s %s" % (opts, extra) if append else opts

        def del_opt(opt, drop):
            """Return ``True`` if option ``opt`` should be dropped or
                ``False`` otherwise.
            """
            # "name" or "name=value"
            if opt in drop:
                return True

            # "name=" wildcard
            if ("%s=" % opt.split('=')[0]) in drop:
                return True

            return False

        def del_opts(opts, drop):
            """Drop specified template supplied kernel options from this
                ``BootEntry``.

                A drop specification matches either a simple name, a name and
                its full value (in which case both must match), or a name,
                followed by '=', indicating that an option with value should
                be dropped regardless of the actual value:

                <name>         drop name
                <name>=        drop name and any value
                <name>=<value> drop name only if its value == value
            """
            return " ".join([o for o in opts.split() if not del_opt(o, drop)])

        if BOOM_ENTRY_OPTIONS in self._entry_data:
            opts = self._entry_data_property(BOOM_ENTRY_OPTIONS)
            if self.bp:
                opts = add_opts(opts, self.bp.add_opts)
                return del_opts(opts, self.bp.del_opts)
            return opts

        if self._osp and self.bp:
            opts = self._apply_format(self._osp.options)
            opts = add_opts(opts, self.bp.add_opts)
            return del_opts(opts, self.bp.del_opts)

        return ""

    @options.setter
    def options(self, options):
        self._entry_data[BOOM_ENTRY_OPTIONS] = options
        self._dirty()

    @property
    def linux(self):
        """The bootable Linux image for this ``BootEntry``.

            :getter: returns the configured ``linux`` image.
            :setter: sets the configured ``linux`` image.
            :type: string
        """
        if not self._osp or BOOM_ENTRY_LINUX in self._entry_data:
            return self._entry_data_property(BOOM_ENTRY_LINUX)

        kernel_path = self._apply_format(self._osp.kernel_pattern)
        return kernel_path

    @linux.setter
    def linux(self, linux):
        self._entry_data[BOOM_ENTRY_LINUX] = linux
        self._dirty()

    @property
    def initrd(self):
        """The loadable initramfs image for this ``BootEntry``.

            :getter: returns the configured ``initrd`` image.
            :getter: sets the configured ``initrd`` image.
            :type: string
        """
        if not self._osp or BOOM_ENTRY_INITRD in self._entry_data:
            return self._entry_data_property(BOOM_ENTRY_INITRD)

        initramfs_path = self._apply_format(self._osp.initramfs_pattern)
        return initramfs_path

    @initrd.setter
    def initrd(self, initrd):
        self._entry_data[BOOM_ENTRY_INITRD] = initrd
        self._dirty()

    @property
    def efi(self):
        """The loadable EFI image for this ``BootEntry``.

            :getter: returns the configured EFI application image.
            :getter: sets the configured EFI application image.
            :type: string
        """
        return self._entry_data_property(BOOM_ENTRY_EFI)

    @efi.setter
    def efi(self, efi):
        self._entry_data[BOOM_ENTRY_EFI] = efi
        self._dirty()

    @property
    def devicetree(self):
        """The devicetree archive for this ``BootEntry``.

            :getter: returns the configured device tree archive.
            :getter: sets the configured device tree archive.
            :type: string
        """
        return self._entry_data_property(BOOM_ENTRY_DEVICETREE)

    @devicetree.setter
    def devicetree(self, devicetree):
        self._entry_data[BOOM_ENTRY_DEVICETREE] = devicetree
        self._dirty()

    @property
    def _entry_path(self):
        id_tuple = (self.machine_id, self.boot_id[0:7], self.version)
        file_name = BOOT_ENTRIES_FORMAT % id_tuple
        return path_join(boom_entries_path(), file_name)

    def write_entry(self, force=False):
        """Write out entry to disk.

            Write out this ``BootEntry``'s data to a file in BLS
            format to the path specified by ``boom_entries_path()``.

            The file will be named according to the entry's key values,
            and the value of the ``BOOT_ENTRIES_FORMAT`` constant.
            Currently the ``machine_id`` and ``version`` keys are used
            to construct the file name.

            If the value of ``force`` is ``False`` and the ``OsProfile``
            is not currently marked as dirty (either new, or modified
            since the last load operation) the write will be skipped.

            :param force: Force this entry to be written to disk even
                          if the entry is unmodified.
            :raises: ``OSError`` if the temporary entry file cannot be
                     renamed, or if setting file permissions on the
                     new entry file fails.
            :returntype: None
        """
        if not self._unwritten and not force:
            return
        entry_path = self._entry_path
        (tmp_fd, tmp_path) = mkstemp(prefix="boom", dir=boom_entries_path())
        with fdopen(tmp_fd, "w") as f:
            # Our original file descriptor will be closed on exit from the
            # fdopen with statement: save a copy so that we can call fdatasync
            # once at the end of writing rather than on each loop iteration.
            tmp_fd = dup(tmp_fd)
            if self._osp:
                # Insert OsIdentifier comment at top-of-file
                f.write("#OsIdentifier: %s\n" % self._osp.os_id)
            for key in [k for k in ENTRY_KEYS if getattr(self, KEY_MAP[k])]:
                if self._comments and key in self._comments:
                    f.write(self._comments[key].rstrip() + '\n')
                # Map Boom key names to BLS entry keys
                key = KEY_MAP[key]
                key_fmt = "%s %s\n"
                key_data = (_transform_key(key), getattr(self, key))
                f.write(key_fmt % key_data)
                f.flush()
        try:
            fdatasync(tmp_fd)
            rename(tmp_path, entry_path)
            chmod(entry_path, BOOT_ENTRY_MODE)
        except Exception as e:
            _log_error("Error writing entry file %s: %s" %
                       (entry_path, e))
            try:
                unlink(tmp_path)
            except:
                pass
            raise e

        self._last_path = entry_path
        self._unwritten = False

        # Add this entry to the list of known on-disk entries
        _add_entry(self)

    def update_entry(self, force=False):
        """Update on-disk entry.

            Update this ``BootEntry``'s on-disk data.

            The file will be named according to the entry's key values,
            and the value of the ``BOOT_ENTRIES_FORMAT`` constant.
            Currently the ``machine_id`` and ``version`` keys are used
            to construct the file name.

            If this ``BootEntry`` previously existed on-disk, and the
            ``boot_id`` has changed due to a change in entry key
            values, the old ``BootEntry`` file will be unlinked once
            the new data has been successfully written. If the entry
            does not already exist then calling this method is the
            equivalent of calling ``BootEntry.write_entry()``.

            If the value of ``force`` is ``False`` and the ``BootEntry``
            is not currently marked as dirty (either new, or modified
            since the last load operation) the write will be skipped.

            :param force: Force this entry to be written to disk even
                          if the entry is unmodified.
            :raises: ``OSError`` if the temporary entry file cannot be
                     renamed, or if setting file permissions on the
                     new entry file fails.
            :returntype: None
        """
        # Cache old entry path
        to_unlink = self._last_path
        self.write_entry(force=force)
        if self._entry_path != to_unlink:
            try:
                unlink(to_unlink)
            except Exception as e:
                _log_error("Error unlinking entry file %s: %s" %
                           (to_unlink, e))

    def delete_entry(self):
        """Remove on-disk BootEntry file.

            Remove the on-disk entry corresponding to this ``BootEntry``
            object. This will permanently erase the current file
            (although the current data may be re-written at any time by
            calling ``write_entry()``).

            :returntype: ``NoneType``
            :raises: ``OsError`` if an error occurs removing the file or
                     ``ValueError`` if the entry does not exist.
        """
        if not path_exists(self._entry_path):
            raise ValueError("Entry does not exist: %s" % self._entry_path)
        try:
            unlink(self._entry_path)
        except Exception as e:
            _log_error("Error removing entry file %s: %s" %
                       (entry_path, e))
            raise

        if not self._unwritten:
            _del_entry(self)


__all__ = [
    # Module constants
    'BOOT_ENTRIES_FORMAT',
    'BOOT_ENTRY_MODE',

    # BootEntry keys
    'BOOM_ENTRY_TITLE',
    'BOOM_ENTRY_VERSION',
    'BOOM_ENTRY_MACHINE_ID',
    'BOOM_ENTRY_LINUX',
    'BOOM_ENTRY_INITRD',
    'BOOM_ENTRY_EFI',
    'BOOM_ENTRY_OPTIONS',
    'BOOM_ENTRY_DEVICETREE',

    # Root device pattern
    'DEV_PATTERN',

    # Boom root device error class
    'BoomRootDeviceError',

    # BootParams and BootEntry objects
    'BootParams', 'BootEntry',

    # Path configuration
    'boom_entries_path',

    # Entry lookup, load, and write functions
    'drop_entries', 'load_entries', 'write_entries', 'find_entries',

    # Formatting
    'min_boot_id_width',

    # Bootloader integration check
    'check_bootloader'
]

# vim: set et ts=4 sw=4 :
