#
# This is the library part of Anita, the Automated NetBSD Installation
# and Test Application.
#

import os
import pexpect
import re
import string
import subprocess
import sys
import time
import urllib
import urlparse

__version__='1.30'

# Your preferred NetBSD FTP mirror site.
# This is used only by the obsolete code for getting releases
# by number, not by the recommended method of getting them by URL.
# See http://www.netbsd.org/mirrors/#ftp for the complete list.

netbsd_mirror_url = "ftp://ftp.netbsd.org/pub/NetBSD/"
#netbsd_mirror_url = "ftp://ftp.fi.NetBSD.org/pub/NetBSD/"

arch_qemu_map = {
    'i386': 'qemu-system-i386',
    'amd64': 'qemu-system-x86_64',
    'sparc': 'qemu-system-sparc',
     # The following ones don't actually work
    'sparc64': 'qemu-system-sparc64',
    'macppc': 'qemu-system-ppc',
}

# External commands we rely on

if os.uname()[0] == 'NetBSD':
    makefs = ["makefs", "-t", "cd9660", "-o", "rockridge"]
elif os.uname()[0] == 'FreeBSD':
    makefs = ["mkisofs", "-r", "-o"]
elif os.uname()[0] == 'Darwin':
    makefs = ["hdiutil", "makehybrid", "-iso", "-o"]
else:
    # Linux distributions differ.  Ubuntu has genisoimage
    # and mkisofs (as an alias of genisoimage); CentOS has
    # mkisofs only.  Use the latter so we work in both.
    makefs = ["mkisofs", "-r", "-o"]

fnull = open(os.devnull, 'w')

# A root shell prompt string that is less likely to appear in the
# console output by accident than the default of "# ".  First, the
# prompt as a quoted string in /bin/sh syntax, with some extra quotes
# in the middle so that an echoed command to set the prompt is not
# mistaken for the prompt itself.  Must end with "# ".
distinctive_prompt_sh_string = "'anita-ro''ot-shell# '"

# A regular expression to match the above prompt.
distinctive_prompt_re = r"anita-root-shell# "

# Return true if the given program (+args) can be successfully run

def try_program(argv):
   try:
       result = subprocess.call(argv, stdout = fnull, stderr = fnull)
       return result == 0
   except OSError:
       return False

# Create a directory if missing

def mkdir_p(dir):
    if not os.path.isdir(dir):
        os.makedirs(dir)

# Run a shell command safely and with error checking

def spawn(command, args):
    #print command, " \\\n    ".join(args[1:])
    ret = os.spawnvp(os.P_WAIT, command, args)
    if ret != 0:
        raise RuntimeError("could not run " + command)

# Wrapper around pexpect.spawn to let us log the command for
# debugging.  Note that unlike os.spawnvp, args[0] is not
# the name of the command.
    
def pexpect_spawn(command, args):
    #print command, " \\\n    ".join(args)
    return pexpect.spawn(command, args)

# Subclass urllib.FancyURLopener so that we can catch
# HTTP 404 errors

class MyURLopener(urllib.FancyURLopener):
    def http_error_default(self, url, fp, errcode, errmsg, headers):
        raise IOError, 'HTTP error code %d' % errcode

def my_urlretrieve(url, filename):
    return MyURLopener().retrieve(url, filename)

# Download a file, cleaning up the partial file if the transfer
# fails or is aborted before completion.

def download_file(file, url):
    try:
        print "Downloading", url + "..."
        my_urlretrieve(url, file)
    except:
        if os.path.exists(file):
            os.unlink(file)
        raise

# Create a file of the given size, containing NULs, without holes.

def make_dense_image(fn, size):
    f = open(fn, "w")
    blocksize = 64 * 1024
    while size > 0:
        chunk = min(size, blocksize)
        f.write("\000" * chunk)
        size = size - chunk
    f.close()

# Parse a size with optional k/M/G/T suffix and return an integer

def parse_size(size):
    m = re.match(r'(\d+)([kMGT])?$', size)
    if not m:
        raise RuntimeError("%s: invalid size" % size)
    size, suffix = m.groups()
    mult = dict(k=1024, M=1024**2, G=1024**3, T=1024**4).get(suffix, 1)
    return int(size) * mult

# Download "url" to the local file "file".  If the file already
# exists locally, do nothing.  If "optional" is true, ignore download
# failures and cache the absence of a missing file by creating a marker
# file with the extension ".MISSING".

def download_if_missing_2(url, file, optional = False):
    if os.path.exists(file):
        return
    if os.path.exists(file + ".MISSING"):
        return
    dir = os.path.dirname(file)
    mkdir_p(dir)
    try:
        download_file(file, url)
    except:
        if optional:
            f = open(file + ".MISSING", "w")
            f.close()
        else:
            raise

# As above, but download a file from the download directory tree
# rooted at "urlbase" into a mirror tree rooted at "dirbase".  The
# file name to download is "relfile", which is relative to both roots.

def download_if_missing(urlbase, dirbase, relfile, optional = False):
    url = urlbase + relfile
    file = os.path.join(dirbase, relfile)
    return download_if_missing_2(url, file, optional)

def download_if_missing_3(urlbase, dirbase, relpath, optional = False):
    url = urlbase + "/".join(relpath)
    file = os.path.join(*([dirbase] + relpath))
    return download_if_missing_2(url, file, optional)

# Map a URL to a directory name.  No two URLs should map to the same
# directory.

def url2dir(url):
    tail = []
    def munge(match):
        index = string.find("/:+-", match.group())
        if index != 0:
            tail.append(chr(0x60 + index) + str(match.start()))
        return "-"
    return "work-" + re.sub("[/:+-]", munge, url) + "+" + "".join(tail)

# Inverse of the above; not used, but included just to show that the
# mapping is invertible and therefore collision-free

class InvalidDir(Exception):
    pass

def dir2url(dir):
    match = re.match(r"(work-)(.*)\+(.*)", dir)
    work, s, tail = match.groups()
    if work != 'work-':
        raise InvalidDir()
    s = re.sub("-", "/", s)
    chars = list(s)
    while True:
        m = re.match(r"([a-z])([0-9]+)", tail)
        if not m:
            break
        c, i = m.groups()
	chars[int(i)] = "/:+-"[ord(c) - 0x60]
        tail = tail[m.end():]
    return "".join(chars)

def check_arch_supported(arch, dist_type):
    if arch_qemu_map.get(arch) is None:
        raise RuntimeError(("'%s' is not the name of a " + \
        "supported NetBSD port") % arch)
    if (arch == 'i386' or arch == 'amd64') and dist_type != 'reltree':
        raise RuntimeError(("NetBSD/%s must be installed from " +
	    "a release tree, not an ISO") % arch)
    if (arch == 'sparc') and dist_type != 'iso':
        raise RuntimeError(("NetBSD/%s must be installed from " +
	"an ISO, not a release tree") % arch)

#############################################################################

# A NetBSD version.
#
# Subclasses should define:
#
#    dist_url(self)
#	the top-level URL for the machine-dependent download tree where 
#       the version can be downloaded, for example,
#       ftp://ftp.netbsd.org/pub/NetBSD/NetBSD-5.0.2/i386/
#
#    mi_url(self)
#       The top-level URL for the machine-independent download tree,
#       for example, ftp://ftp.netbsd.org/pub/NetBSD/NetBSD-5.0.2/
#
#    default_workdir(self)
#        a file name component identifying the version, for use in
#        constructing a unique, version-specific working directory
#
#    arch(self)
#        the name of the machine architecture the version is for,
#        e.g., i386

def make_item(t):
    d = dict(zip(['filename', 'label', 'install'], t[0:3]))
    if isinstance(t[3], list):
        d['group'] = make_set_dict_list(t[3])
    else:
        d['optional'] = t[3]
    return d

def make_set_dict_list(list_):
    return [make_item(t) for t in list_]

def flatten_set_dict_list(list_):
    def item2list(item):
        group = item.get('group')
        if group:
            return group
        else:
            return [item]
    return sum([item2list(item) for item in list_], [])

class Version:
    # Information about the available installation file sets.  As the
    # set of sets (sic) has evolved over time, this actually represents
    # the union of those sets of sets, in other words, this list should
    # contain all currently and historically known sets.
    #
    # This list is used for to determine
    # - Which sets we should attempt to download
    # - Which sets we should install by default
    #
    # Each array element is a tuple of four fields:
    #   - the file name
    #   - the label used by sysinst
    #   - a flag indicating that the set should be installed by default
    #   - a flag indicating that the set is not present in all versions
    #
    
    sets = make_set_dict_list([
      [ 'kern-GENERIC', 'Kernel (GENERIC)', 1, 0 ],
      [ 'kern-GENERIC.NOACPI', 'Kernel (GENERIC.NOACPI)', 0, 1 ],
      [ 'modules', 'Kernel Modules', 1, 1 ],
      [ 'base', 'Base', 1, 0 ],
      [ 'etc', 'System (/etc)', 1, 0 ],
      [ 'comp', 'Compiler Tools', 1, 0 ],
      [ 'games', 'Games', 0, 0 ],
      [ 'man', 'Online Manual Pages', 0, 0 ],
      [ 'misc', 'Miscellaneous', 1, 0 ],
      [ 'tests', 'Test programs', 1, 1 ],
      [ 'text', 'Text Processing Tools', 0, 0 ],
      [ '_x11', 'X11 sets', 0, [
          ['xbase',   'X11 base and clients', 0, 1 ],
          ['xcomp',   'X11 configuration', 0, 1 ],
          ['xetc',    'X11 fonts', 0, 1 ],
          ['xfont',   'X11 servers', 0, 1 ],
          ['xserver', 'X11 programming', 0, 1 ],
      ]],
      [ '_src', 'Source sets', 0, [
          ['syssrc', 'Kernel sources', 0, 1],
          ['src', 'Base sources', 0, 1],
          ['sharesrc', 'Share sources', 0, 1],
          ['gnusrc', 'GNU sources', 0, 1],
          ['xsrc', 'X11 sources', 0, 1],
          ['debug', 'debug sets', 0, 1],
          ['xdebug', 'debug X11 sets', 0, 1],
      ]]
    ])

    flat_sets = flatten_set_dict_list(sets)

    def __init__(self, sets = None):
        self.tempfiles = []
        if sets is not None:
            if not any([re.match('kern-', s) for s in sets]):
                raise RuntimeError("no kernel set specified");
            # Create a Python set containing the names of the NetBSD sets we
            # want for O(1) lookup.  Yes, the multiple meansings of the word
            # "set" here are confusing.
            sets_wanted = set(sets)
            for required in ['base', 'etc']:
                if not required in sets_wanted:
                    raise RuntimeError("the '%s' set is required", required);
            for s in self.flat_sets:
                s['install'] = (s['filename'] in sets_wanted)
                sets_wanted.discard(s['filename'])
            if len(sets_wanted):
                raise RuntimeError("no such set: " + sets_wanted.pop())
        
    def set_workdir(self, dir):
        self.workdir = dir
    # The directory where we mirror files needed for installation
    def download_local_mi_dir(self):
        return self.workdir + "/download/"
    def download_local_arch_dir(self):
        return self.download_local_mi_dir() + self.arch() + "/"
    # The path to the install ISO image
    def iso_path(self):
        return os.path.join(self.workdir, self.iso_name())
    # The directory for the install floppy images
    def floppy_dir(self):
        return os.path.join(self.download_local_arch_dir(),
	    "installation/floppy")
    def boot_iso_dir(self):
        return os.path.join(self.download_local_arch_dir(),
	    "installation/cdrom")
    def boot_from_default(self):
        return None
    def scratch_disk(self):
        arch = self.arch()
        if arch == 'i386' or arch == 'amd64':
            return "wd1d"
        else:
            return "sd1c"

    def xen_kernel(self):
        arch = self.arch()
        if arch == 'i386':
            return 'netbsd-XEN3PAE_DOMU.gz'
        elif arch == 'amd64':
            return 'netbsd-XEN3_DOMU.gz'
        else:
            return None
            
    def xen_install_kernel(self):
        arch = self.arch()
        if arch == 'i386':
            return 'netbsd-INSTALL_XEN3PAE_DOMU.gz'
        elif arch == 'amd64':
            return 'netbsd-INSTALL_XEN3_DOMU.gz'
        else:
            return None

    # The list of boot floppies we should try downloading;
    # not all may actually exist.  amd64 currently has five,
    # i386 has three, and older versions may have fewer.
    # Add a couple extra to accomodate future growth.
    def potential_floppies(self):
        return ['boot-com1.fs'] + ['boot%i.fs' % i for i in range(2, 8)]

    # The list of boot floppies we actually have
    def floppies(self):
        return [f for f in self.potential_floppies() \
            if os.path.exists(os.path.join(self.floppy_dir(), f))]

    def boot_isos(self):
        return ['boot-com.iso']

    def cleanup(self):
        for fn in self.tempfiles:
            os.unlink(fn)

    def set_path(self, setname):
        if re.match(r'.*src$', setname):
            return ['source', 'sets', setname + '.tgz']
        else:
            return [self.arch(), 'binary', 'sets', setname + '.tgz']
    
    # Download this release
    # The ISO class overrides this to download the ISO only
    def download(self):
        # Depending on the NetBSD version, there may be two or more
        # boot floppies.  Treat any floppies past the first two as
        # optional files.
        i = 0
        for floppy in self.potential_floppies():
            download_if_missing_3(self.dist_url(),
                self.download_local_arch_dir(),
                ["installation", "floppy", floppy],
                i >= 2)
            i = i + 1

	for bootcd in (self.boot_isos()):
            download_if_missing_3(self.dist_url(),
                self.download_local_arch_dir(),
                ["installation", "cdrom", bootcd],
		True)

        for set in self.flat_sets:
            if set['install']:
                download_if_missing_3(self.mi_url(),
                                      self.download_local_mi_dir(),
                                      self.set_path(set['filename']),
                                      set['optional'])

    # Create an install ISO image to install from
    def make_iso(self):
        self.download()
        spawn(makefs[0], makefs + \
            [self.iso_path(), self.download_local_mi_dir()])
        self.tempfiles.append(self.iso_path())

    # Get the architecture name.  This is a hardcoded default for use
    # by the obsolete subclasses; the "URL" class overrides it.
    def arch(self):
        return "i386"

    # Backwards compatibility with Anita 1.2 and older
    def install(self):
        Anita(dist = self).install()
    def boot(self):
        Anita(dist = self).boot()
    def interact(self):
        Anita(dist = self).interact()

# Subclass for versions where we pass in the version number explicitly

class NumberedVersion(Version):
    def __init__(self, ver, **kwargs):
        Version.__init__(self, **kwargs)
        self.ver = ver
    # The file name of the install ISO (sans directory)
    def iso_name(self):
        if re.match("^[3-9]", self.ver) is not None:
            return "i386cd-" + self.ver + ".iso"
        else:
            return "i386cd.iso"
    # The directory for files related to this release
    def default_workdir(self):
        return "netbsd-" + self.ver

# An official NetBSD release

class Release(NumberedVersion):
    def __init__(self, ver, **kwargs):
        NumberedVersion.__init__(self, ver, **kwargs)
        pass
    def mi_url(self):
        return netbsd_mirror_url + "NetBSD-" + self.ver + "/"
    def dist_url(self):
        return self.mi_url() + self.arch() + "/"

# A daily build

class DailyBuild(NumberedVersion):
    def __init__(self, branch, timestamp, **kwargs):
        ver = re.sub("^netbsd-", "", branch)
        NumberedVersion.__init__(self, ver, **kwargs)
        self.timestamp = timestamp
    def default_workdir(self):
        return NumberedVersion.default_workdir(self) + "-" + self.timestamp
    def dist_url(self):
        branch = re.sub("[\\._]", "-", self.ver)
        if re.match("^[0-9]", branch):
            branch = "netbsd-" + branch
        return "http://ftp.netbsd.org/pub/NetBSD-daily/" + \
            branch + "/" + self.timestamp + "/i386/"

# A local build

class LocalBuild(NumberedVersion):
    def __init__(self, ver, release_path, **kwargs):
        NumberedVersion.__init__(self, ver, **kwargs)
        self.release_path = release_path
    def dist_url(self):
        return "file://" + self.release_path + "/i386/"

# The top-level URL of a release tree

class URL(Version):
    def __init__(self, url, **kwargs):
        Version.__init__(self, **kwargs)
        self.url = url
	match = re.match(r'(^.*/)([^/]+)/$', url)
	if match is None:
            raise RuntimeError(("URL '%s' doesn't look like the URL of a " + \
	    "NetBSD distribution") % url)
        self.url_mi_part = match.group(1)
        self.m_arch = match.group(2)
	check_arch_supported(self.m_arch, 'reltree')
    def dist_url(self):
        return self.url
    def mi_url(self):
        return self.url_mi_part
    def iso_name(self):
        return "install_tmp.iso"
    def default_workdir(self):
        return url2dir(self.url)
    def arch(self):
        return self.m_arch

# A local release directory

class LocalDirectory(URL):
    def __init__(self, dir, **kwargs):
        # This could be optimized to avoid copying the files
        URL.__init__(self, "file://" + dir, **kwargs)

# An URL or local file name pointing at an ISO image

class ISO(Version):
    def __init__(self, iso_url, **kwargs):
        Version.__init__(self, **kwargs)
        if re.match(r'/', iso_url):
	    self.m_iso_url = "file://" + iso_url
	    self.m_iso_path = iso_url
	else:
            self.m_iso_url = iso_url
	    self.m_iso_path = None
	# We can't determine the final ISO file name yet because the work
	# directory is not known at this point, but we can precalculate the
	# basename of it.
	self.m_iso_basename = os.path.basename(
	    urllib.url2pathname(urlparse.urlparse(iso_url)[2]))
	m = re.match(r"(.*)cd.*iso|NetBSD-[0-9\._A-Z]+-(.*).iso", self.m_iso_basename)
	if m is None:
            raise RuntimeError("cannot guess architecture from ISO name '%s'"
	        % self.m_iso_basename)
        if m.group(1) is not None:
            self.m_arch = m.group(1)
        if m.group(2) is not None:
            self.m_arch = m.group(2)
	check_arch_supported(self.m_arch, 'iso')
    def iso_path(self):
        if self.m_iso_path is not None:
	    return self.m_iso_path
	else:
            return os.path.join(self.download_local_arch_dir(),
	        self.m_iso_basename)
    def default_workdir(self):
         return url2dir(self.m_iso_url)
    def make_iso(self):
        self.download()
    def download(self):
        if self.m_iso_path is None:
            download_if_missing_2(self.m_iso_url, self.iso_path())
	else:
	    mkdir_p(self.workdir)
    def arch(self):
        return self.m_arch
    def boot_from_default(self):
        return 'cdrom-with-sets'

#############################################################################

# Helper class for killing the DomU when the last reference to the
# child process is dropped
    
class DomUKiller:
    def __init__(self, frontend, name):
        self.name = name
        self.frontend = frontend
    def __del__(self):
        print "destroying domU", self.name
        spawn(self.frontend, [self.frontend, "destroy", self.name])

def vmm_is_xen(vmm):
    return vmm == 'xm' or vmm == 'xl'

class Anita:
    def __init__(self, dist, workdir = None, vmm = 'qemu', vmm_args = None,
        disk_size = None, memory_size = None, persist = False, boot_from = None):
        self.dist = dist
        if workdir:
            self.workdir = workdir
        else:
            self.workdir = dist.default_workdir()

	# Set the default disk size if none was given.
        if disk_size is None:
	    disk_size = "768M"
	self.disk_size = disk_size

	# Set the default memory size if none was given.
        if memory_size is None:
            if dist.arch() == 'amd64':
                memory_size = "128M"
            else:
                memory_size = "32M"
	self.memory_size_bytes = parse_size(memory_size)

        self.persist = persist

	self.boot_from = boot_from

	self.qemu = arch_qemu_map.get(dist.arch())
	if self.qemu is None:
            raise RuntimeError("NetBSD port '%s' is not supported" %
	        dist.arch())

        if self.qemu == 'qemu-system-i386' and \
           not try_program(['qemu-system-i386', '--version']) \
           and try_program(['qemu', '--version']): \
               self.qemu = 'qemu'

        # Backwards compatibility
        if vmm == 'xen':
            vmm = 'xm'

        self.vmm = vmm

        if vmm_args is None:
            vmm_args = []
        self.extra_vmm_args = vmm_args

    # The path to the NetBSD hard disk image
    def wd0_path(self):
        return os.path.join(self.workdir, "wd0.img")

    # Return the memory size rounded up to whole megabytes
    def memory_megs(self):
        megs = (self.memory_size_bytes + 2 ** 20 - 1) / 2 ** 20
        if megs != self.memory_size_bytes / 2 **20:
            print >>sys.stderr, \
                "warning: rounding up memory size of %i bytes to %i megabytes" \
                % (self.memory_size_bytes, megs)
        return megs

    def configure_child(self, child):
	# pexpect 2.1 uses "child.logfile", but pexpect 0.999nb1 uses
	# "child.log_file", so we set both variables for portability
        child.logfile = sys.stdout
        child.log_file = sys.stdout
        child.timeout = 300
        child.setecho(False)
        # Xen installs sometimes fail if we don't increase this
	# from the default of 0.1 seconds
        child.delayafterclose = 5.0
        # Also increase this just in case
        child.delayafterterminate = 5.0
	self.child = child

    def start_qemu(self, vmm_args, snapshot_system_disk):
        child = pexpect_spawn(self.qemu, [
	    "-m", str(self.memory_megs()),
            "-drive", "file=%s,index=0,media=disk,snapshot=%s" %
	        (self.wd0_path(), ("off", "on")[snapshot_system_disk]),
            "-nographic"
            ] + vmm_args + self.extra_vmm_args)
        self.configure_child(child)
        return child

    def xen_disk_arg(self, path, devno = 0, writable = True):
        if self.vmm == 'xm':
            return "disk=file:%s,0x%x,%s" % (path, devno, "rw"[writable])
        else: # xl
            return "disk=file:%s,xvd%s,%s" % (path, chr(ord('a') + devno), "rw"[writable])

    def qemu_disk_args(self, path, devno = 0, writable = True, snapshot = False):
        return ["-drive", "file=%s,index=%i,media=disk,snapshot=%s" % (path, devno, ["off", "on"][snapshot])]

    def qemu_cdrom_args(self, path, devno):
        return ["-drive", "file=%s,index=%i,media=cdrom" % (path, devno)]

    def string_arg(self, name, value):
        if self.vmm == 'xm':    
            return '%s=%s' % (name, value)
        else: # xl
            return '%s="%s"' % (name, value)	
    
    def start_xen_domu(self, vmm_args):
        frontend = self.vmm
        name = "anita-%i" % os.getpid()
        args = [
	    frontend,
	    "create",
            "-c",
            "/dev/null",
            self.xen_disk_arg(os.path.abspath(self.wd0_path()), 0, True),
	    "memory=" + str(self.memory_megs()),
            self.string_arg('name', name)
        ] + vmm_args + self.extra_vmm_args
        
        # Multiple "disk=" arguments are no longer supported with xl;
        # combine them
        if self.vmm == 'xl':
            disk_args = []
            no_disk_args = []
            for arg in args:
                if arg.startswith('disk='):
                    disk_args.append(arg[5:])
                else:
                    no_disk_args.append(arg)
            args = no_disk_args + [ "disk=[%s]" % (','.join(["'%s'" % arg for arg in disk_args]))]
        
        child = pexpect_spawn(args[0], args[1:])
        self.configure_child(child)
        # This is ugly; we reach into the child object and set an
        # additional attribute.  The name of the attribute,
        # "garbage_collector" below, is arbitrary, but must not
        # conflict with any existing attribute of the child
        # object.  Its purpose is only to hold a reference to the
        # DomUKiller object, such that when the child object is
        # destroyed, the destructor of the DomUKiller object
        # is also invoked.
        child.garbage_collector = DomUKiller(frontend, name)
        return child

    def _install(self):
        # Download or build the install ISO
        self.dist.set_workdir(self.workdir)
        self.dist.make_iso()

	arch = self.dist.arch()

	# Create a disk image file
        print "Creating disk image...",
        sys.stdout.flush()
        make_dense_image(self.wd0_path(), parse_size(self.disk_size))
        print "done."

	# The name of the CD-ROM device holding the sets
	cd_device = None

        if vmm_is_xen(self.vmm):
            # Download XEN kernels
            xenkernels = [k for k in [self.dist.xen_kernel(), self.dist.xen_install_kernel()] if k]
            for kernel in xenkernels:
                download_if_missing_3(self.dist.dist_url(),
                        self.dist.download_local_arch_dir(),
                        ["binary", "kernel", kernel],
                        True)
            
            vmm_args = [
                self.string_arg('kernel', os.path.abspath(os.path.join(self.dist.download_local_arch_dir(),
                    "binary", "kernel", self.dist.xen_install_kernel()))),
                self.xen_disk_arg(os.path.abspath(self.dist.iso_path()), 1, False)
            ]
            child = self.start_xen_domu(vmm_args)
	    cd_device = 'xbd1d';
            
        elif self.vmm == 'qemu':
	    # Determine what kind of media to boot from.
	    if self.boot_from is None:
	        self.boot_from = self.dist.boot_from_default()
	    boot_cd_path = os.path.join(self.dist.boot_iso_dir(), self.dist.boot_isos()[0])
	    if self.boot_from is None:
	        self.boot_from = 'floppy'

            # Set up VM arguments based on the chosen boot media
	    if self.boot_from == 'cdrom':
	        vmm_args = self.qemu_cdrom_args(
		    boot_cd_path, 1)
                vmm_args += self.qemu_cdrom_args(self.dist.iso_path(), 2)
                vmm_args += ["-boot", "d"]
		cd_device = 'cd1a';
            elif self.boot_from == 'floppy':
                vmm_args = self.qemu_cdrom_args(self.dist.iso_path(), 1)
                floppy_paths = [ os.path.join(self.dist.floppy_dir(), f) \
                    for f in self.dist.floppies() ]
                if len(floppy_paths) == 0:
                    raise RuntimeError("found no boot floppies")
                vmm_args += ["-fda", floppy_paths[0], "-boot", "a"]
		cd_device = 'cd0a';		
            elif self.boot_from == 'cdrom-with-sets':
	        # Single CD
                vmm_args = self.qemu_cdrom_args(self.dist.iso_path(), 1)
                vmm_args += ["-boot", "d"]
		cd_device = 'cd0a';

            child = self.start_qemu(vmm_args, snapshot_system_disk = False)
        else:
            raise RuntimeError('unknown vmm')
                               
	term = None

        # Do the floppy swapping dance and other pre-sysinst interaction
	
        floppy0_name = None
	while True:
	    # NetBSD/i386 will prompt for a terminal type if booted from a
	    # CD-ROM, but not when booted from floppies.  Sigh.
	    child.expect(
	        # Group 1-2
	        "(insert disk (\d+), and press return...)|" +
		# Group 3
		"(a: Installation messages in English)|" +
		# Group 4
		"(Terminal type)|" +
		# Group 5
                "(Installation medium to load the additional utilities from: )|"
		# Group 6
		"(1. Install NetBSD)"
		)
	    if child.match.group(1):
		# We got the "insert disk" prompt
		# There is no floppy 0, hence the "- 1"
		floppy_index = int(child.match.group(2)) - 1

		# Escape into qemu command mode to switch floppies
		child.send("\001c")
		# We used to wait for a (qemu) prompt here, but qemu 0.9.1
		# no longer prints it
		# child.expect('\(qemu\)')
		if not floppy0_name:
		    # Between qemu 0.9.0 and 0.9.1, the name of the floppy
		    # device accepted by the "change" command changed from
		    # "fda" to "floppy0" without any provision for backwards
		    # compatibility.  Deal with it.  Also deal with the fact
		    # that as of qemu 0.15, "info block" no longer prints
		    # "type=floppy" for floppy drives.
		    child.send("info block\n")
		    child.expect(r'\n(fda|floppy0): ')
		    floppy0_name = child.match.group(1)
		# Now we can change the floppy
		child.send("change %s %s\n" %
		    (floppy0_name, floppy_paths[floppy_index]))
		# Exit qemu command mode
		child.send("\001c\n")
	    elif child.match.group(3):
	        # "Installation messages in English"
		break
	    elif child.match.group(4):
	        # "Terminal type"
		child.send("xterm\n")
		term = "xterm"
		continue
	    elif child.match.group(5):
	        # "Installation medium to load the additional utilities from"
		# (SPARC)
		child.send("cdrom\n")
	        child.expect("CD-ROM device to use")
		child.send("\n")
	        child.expect("Path to instfs.tgz")
		child.send("\n")
	        child.expect("Terminal type")
		# The default is "sun", but anita is more likely to run
		# in an xterm or some other ansi-like terminal than on
		# a sun console.
		child.send("xterm\n")
		term = "xterm"
	        child.expect("nstall/Upgrade")
		child.send("I\n")
	    elif child.match.group(6):
	        # "1. Install NetBSD"
	        child.send("1\n");

        # Confirm "Installation messages in English"
        child.send("\n")

	# i386 and amd64 ask for keyboard type here; sparc doesn't
	while True:
            child.expect("(Keyboard type)|(a: Install NetBSD to hard disk)|" +
	        "(Shall we continue)")
            if child.match.group(1) or child.match.group(2):
	        child.send("\n")
	    elif child.match.group(3):
                child.expect("b: Yes")
	        child.send("b\n")
	        break
            else:
	        raise AssertionError

        # Depending on the number of disks attached, we get either
        # "Found only one disk" followed by "Hit enter to continue",
        # or "On which disk do you want to install".
        child.expect("(Hit enter to continue)|" +
	    "(On which disk do you want to install)")
	if child.match.group(1):
            child.send("\n")
	elif child.match.group(2):
	    child.send("a\n")
        else:
            raise AssertionError

        # Custom installation is choice "d" in 6.0,
        # but choice "c" or "b" in older versions
        # We could use "Minimal", but it doesn't exist in
        # older versions.
        child.expect("([a-z]): Custom installation")
        child.send(child.match.group(1) + "\n")

        # Enable/disable sets.  

        # Keep track of sets we have already handled, by label.
        # This is needed so that parsing a pop-up submenu is not
        # confused by earlier output echoing past choices.
        labels_seen = set()

        def choose_sets(set_list):
            sets_this_screen = []
            # First parse the set selection screen or popup; it's messy.
            while True:
                # Match a letter-label pair, like "h: Compiler Tools",
                # followed by an installation status of Yes, No, All,
                # or None.  The label can be separated from the "Yes/No" 
                # field either by spaces (at least two, so that there can
                # be single spaces within the label), or by a cursor
                # positioning escape sequence.  In the case of the
                # "X11 fonts" set, we strangely get both a single space
                # and an escape sequence, which seems disoptimal.
                #
                # Alternatively, match the special letter "x: " which
                # is not followed by an installation status.
                child.expect(
                    "(?:([a-z]): ([^ \x1b]+(?: [^ \x1b]+)*)(?:(?:\s\s+)|(?:\s?\x1b\[\d+;\d+H))(Yes|No|All|None))|(x: )")
                (letter, label, yesno, exit) = child.match.groups()
                if exit:
                    if len(sets_this_screen) != 0:
                        break
                else:
                    for set in set_list:
                        # Could use RE match here for more flexibility
                        if label == set['label'] and label not in labels_seen:
                            sets_this_screen.append({
                                'set': set,
                                'letter': letter,
                                'state': yesno
                            })
                            labels_seen.add(label)

            # Then make the actual selections
            for item in sets_this_screen:
                set = item['set']
                enable = set['install']
                state = item['state']
                group = set.get('group')
                if (enable and state == "No" or \
                       not enable and state == "Yes") \
                       or group:
                    child.send(item['letter'] + "\n")
                if group:
                    # Recurse to handle sub-menu
                    choose_sets(group)

            # Exit the set selection menu
            child.send("x\n")

        choose_sets(self.dist.sets)

        while True:
            # On non-Xen i386/amd64 we first get group 1 or 2,
            # then group 3; on sparc and Xen, we just get group 3.
	    child.expect("(a: This is the correct geometry)|" +
	        "(a: Use one of these disks)|" +
                "(a: Set sizes of NetBSD partitions)")
            if child.match.group(1) or child.match.group(2):
                if child.match.group(1):
                    child.send("\n")
                elif child.match.group(2):
                    child.send("a\n")
                    child.expect("Choose disk")
                    child.send("0\n")
                child.expect("b: Use the entire disk")
                child.send("b\n")
                child.expect("Do you want to install the NetBSD bootcode")
                child.expect("a: Yes")
                child.send("\n")
            elif child.match.group(3):
                child.send("a\n")
                break
	    else:
		raise AssertionError

        child.expect("Accept partition sizes")
        # Press cursor-down enough times to get to the end of the list,
	# to the "Accept partition sizes" entry, then press
        # enter to continue.  Previously, we used control-N ("\016"),
        # but if it gets echoed (which has happened), it is interpreted by
        # the terminal as "enable line drawing character set", leaving the
        # terminal in an unusable state.
	if term == 'xterm':
	    # For unknown reasons, when using a terminal type of "xterm",
	    # sysinst puts the terminal in "application mode", causing the
	    # cursor keys to send a different escape sequence than the default.
	    cursor_down = "\033OB"
	else:
	    # Use the default ANSI cursor-down escape sequence
	    cursor_down = "\033[B"
        child.send(cursor_down * 8 + "\n")

        child.expect("x: Partition sizes ok")
        child.send("\n")
        child.expect("Please enter a name for your NetBSD disk")
        child.send("\n")

        # "This is your last chance to quit this process..."
        child.expect("Shall we continue")
        child.expect("b: Yes")
        child.send("b\n")

	# newfs is run at this point

	# Many different things can happen at this point:
        #
        # Versions older than 2009/08/23 21:16:17 will display a menu
	# for choosing the extraction verbosity
	#
	# Versions older than 2010/03/30 20:09:25 will display a menu for
	# choosing the CD-ROM device (newer versions will choose automatically)
	#
        # Versions older than Fri Apr 6 23:48:53 2012 UTC will ask
	# you to "Please choose the timezone", wheras newer ones will
	# instead as you to "Configure the additional items".
	#
	# At various points, we may or may not get "Hit enter to continue"
	# prompts (and some of them seem to appear nondeterministically)
	#
	# i386/amd64 can ask whether to use normal or serial console bootblocks
        #
	# Try to deal with all of the possible options.
        #
        # We specify a longer timeout than the default here, because the
        # set extraction can take a long time on slower machines.
	#
        # It has happened (at least with NetBSD 3.0.1) that sysinst paints the
	# screen twice.  This can cause problem because we will then respond
	# twice, and the second response will be interpreted as a response to
	# a subsequent prompt.  Therefore, we check whether the match is the
	# same as the previous one and ignore it if so.
	#
        # OTOH, -current as of 2009.08.23.20.57.40 will issue the message "Hit 
        # enter to continue" twice in a row, first as a result of MAKEDEV 
        # printing a warning messages "MAKEDEV: dri0: unknown device", and 
        # then after "sysinst will give you the opportunity to configure 
        # some essential things first".  We match the latter text separately
        # so that the "Hit enter to continue" matches are not consecutive.
        #
        # The changes of Apr 6 2012 broght with them a new redraw problem,
	# which is worked around using the seen_essential_things variable.
	#
	prevmatch = []
	seen_essential_things = 0
	loop = 0
        while True:
	    loop = loop + 1
	    if loop == 20:
	        raise RuntimeError("loop detected")
	    child.expect(
	                 # Group 1
			 "(a: Progress bar)|" +
			 # Group 2
                         "(a: CD-ROM)|" +
			 # Group 3-4
                         "(([cx]): Continue)|" +
			 # Group 5
                         "(Hit enter to continue)|" +
			 # Group 6
                         "(b: Use serial port com0)|" +
			 # Group 7
                         "(Please choose the timezone)|" +
			 # Group 8
                         "(essential things)|" +
			 # Group 9
			 "(Configure the additional items)|" +
			 # Group 10
			 "(Multiple CDs found)",
			 3600)

	    if child.match.groups() == prevmatch:
	        continue
	    prevmatch = child.match.groups()
	    if child.match.group(1):
	        # (a: Progress bar)
		child.send("\n")
	    elif child.match.group(2):
	        # (a: CD-ROM)
		child.send("\n")
            elif child.match.group(3):
	        # CDROM device selection
                if cd_device != 'cd0a':
                    child.send("a\n" + cd_device + "\n")
	        # (([cx]): Continue)
		# In 3.0.1, you type "c" to continue, whereas in -current,
		# you type "x".  Handle both cases.
		child.send(child.match.group(4) + "\n")
	    elif child.match.group(5):
	        # (Hit enter to continue)
		if seen_essential_things >= 2:
		    # This must be a redraw
		    pass
		else:
		    child.send("\n")
            elif child.match.group(6):
	        # (b: Use serial port com0)
                child.send("bx\n")
	    elif child.match.group(7):
	        # (Please choose the timezone)
	        # "Press 'x' followed by RETURN to quit the timezone selection"
		child.send("x\n")
		# The strange non-deterministic "Hit enter to continue" prompt has
		# also been spotted after executing the sed commands to set the
		# root password cipher, with 2010.10.27.10.42.12 source.
		while True:
		    child.expect("(([a-z]): DES)|(root password)|(Hit enter to continue)")
		    if child.match.group(1):
			# DES
			child.send(child.match.group(2) + "\n")
		    elif child.match.group(3):
			# root password
			break
		    elif child.match.group(4):
			# (Hit enter to continue)
			child.send("\n")
		    else:
			raise AssertionError
		# Don't set a root password
		child.expect("b: No")
		child.send("b\n")
		child.expect("a: /bin/sh")
		child.send("\n")

		# "The installation of NetBSD-3.1 is now complete.  The system
		# should boot from hard disk.  Follow the instructions in the
		# INSTALL document about final configuration of your system.
		# The afterboot(8) manpage is another recommended reading; it
		# contains a list of things to be checked after the first
		# complete boot."
		#
		# We are supposed to get a single "Hit enter to continue"
		# prompt here, but sometimes we get a weird spurious one
		# after running chpass above.

		while True:        
		    child.expect("(Hit enter to continue)|(x: Exit)")
		    if child.match.group(1):
			child.send("\n")
		    elif child.match.group(2):
			child.send("x\n")
			break
		    else:
			raise AssertionError                
		break
	    elif child.match.group(8):
                # (essential things)
		seen_essential_things += 1
	    elif child.match.group(9):
	        # (Configure the additional items)
		child.expect("x: Finished configuring")
		child.send("x\n")
		break
	    elif child.match.group(10):
	        # (Multiple CDs found)
		# This happens if we have a boot CD and a CD with sets;
		# we need to choose the latter.
	        child.send("b\n")
	    else:
	        raise AssertionError

        # Installation is finished, halt the system.
	# Historically, i386 and amd64, you get a root shell,
	# while sparc just halts.
	# Since Fri Apr 6 23:48:53 2012 UTC, you are kicked
	# back into the main menu.

	prevmatch = []
	while True:
            child.expect("(Hit enter to continue)|(x: Exit Install System)|(#)|(halting machine)|(halted by root)")
	    if child.match.groups() == prevmatch:
	        continue
	    prevmatch = child.match.groups()
	    if child.match.group(1):
	        child.send("\n")
	    elif child.match.group(2):
		# Back in menu
		child.send("x\n")
	    elif child.match.group(3):
	        # Root shell prompt
	        child.send("halt\n")
	    else:
	        # group 4 or 5: halted
		break

        child.close()
        # Make sure all refs go away
        child = None
        self.child = None
        self.dist.cleanup()

    # Install NetBSD if not installed already

    def install(self):
        # Already installed?
        if os.path.exists(self.wd0_path()):
            return
        try:
            self._install()
        except:
            if os.path.exists(self.wd0_path()):
                os.unlink(self.wd0_path())
            raise

    # Boot the virtual machine (installing it first if it's not
    # installed already).  The vmm_args argument applies when
    # booting, but not when installing.
    def boot(self, vmm_args = None):
        if vmm_args is None:
            vmm_args = []

        # This is needed only for Xen, where we get the kernel
        # from the dist rather than the installed image
        self.dist.set_workdir(self.workdir)

        self.install()

        if self.vmm == 'qemu':
            child = self.start_qemu(vmm_args, snapshot_system_disk = not self.persist)
            # "-net", "nic,model=ne2k_pci", "-net", "user"
        elif vmm_is_xen(self.vmm):
            child = self.start_xen_domu(vmm_args + [self.string_arg('kernel',
                os.path.abspath(os.path.join(self.dist.download_local_arch_dir(),
                             "binary", "kernel", self.dist.xen_kernel())))])
        else:
            raise RuntimeError('unknown vmm')
            
        child.expect("login:")
        # Can't close child here because we still need it if called from
	# interact()
	self.child = child
        return child

    # Deprecated
    def interact(self):
        child = self.boot()
        console_interaction(child)

    def run_tests(self, timeout = 7200):
	# Create a scratch disk image for exporting test results from the VM.
        # The results are stored in tar format because that is more portable
        # and easier to manipulate than a file system image, especially if the
        # host is a non-NetBSD system.
	scratch_disk_path = os.path.join(self.workdir, "tests-results.img")
        if vmm_is_xen(self.vmm):
            scratch_disk = 'xbd1d'
        else:
            scratch_disk = self.dist.scratch_disk()
        mkdir_p(self.workdir)

	scratch_image_megs = 100
        make_dense_image(scratch_disk_path, parse_size('%dM' % scratch_image_megs))
	# Leave a 10% safety margin
	max_result_size_k = scratch_image_megs * 900

        if vmm_is_xen(self.vmm):
            scratch_disk_args = [self.xen_disk_arg(os.path.abspath(scratch_disk_path), 1, True)]
        elif self.vmm == 'qemu':
            scratch_disk_args = self.qemu_disk_args(os.path.abspath(scratch_disk_path), 1, True, False)
        else:
            raise RuntimeError('unknown vmm')

        child = self.boot(scratch_disk_args)
	login(child)

        have_kyua = shell_cmd(child,
                              "grep -q 'MKKYUA.*=.*yes' /etc/release") == 0
        if have_kyua:
	    test_cmd = (
		"kyua " + 
		    "--loglevel=error " +
		    "--logfile=/tmp/tests/kyua-test.log " +
		    "test " +
		    "--store=/tmp/tests/store.db; " +
		"echo $? >/tmp/tests/test.status; " +
		"kyua " +
		    "report " +
		    "--store=/tmp/tests/store.db " +
		    "| tail -n 3; " +
		"kyua " +
		    "--loglevel=error " +
		    "--logfile=/tmp/tests/kyua-report-html.log " +
		    "report-html " +
		    "--store=/tmp/tests/store.db " +
		    "--output=/tmp/tests/html; ")
        else:
	    atf_aux_files = ['/usr/share/xsl/atf/tests-results.xsl',
			     '/usr/share/xml/atf/tests-results.dtd',
			     '/usr/share/examples/atf/tests-results.css']
 	    test_cmd = (
		"{ atf-run; echo $? >/tmp/tests/test.status; } | " +
		"tee /tmp/tests/test.tps | " +
		"atf-report -o ticker:- -o xml:/tmp/tests/test.xml; " +
                "(cd /tmp && for f in %s; do cp $f tests/; done;); " % ' '.join(atf_aux_files))

        exit_status = shell_cmd(child,
	    "df -k | sed 's/^/df-pre-test /'; " +
	    "mkdir /tmp/tests && " +
	    "cd /usr/tests && " +
	    test_cmd +
	    "{ cd /tmp && " +
                # Make sure the files will fit on the scratch disk
                "test `du -sk tests | awk '{print $1}'` -lt %d && " % max_result_size_k +
                # To guard against accidentally overwriting the wrong
                # disk image, check that the disk contains nothing
                # but nulls.
                "test `</dev/r%s tr -d '\\000' | wc -c` = 0 && " % scratch_disk +
                # "disklabel -W /dev/rwd1d && " +
                "tar cf /dev/r%s tests; " % scratch_disk +
            "}; " +
	    "df -k | sed 's/^/df-post-test /'; " +
	    "ps -glaxw | sed 's/^/ps-post-test /'; " +
            "sh -c 'exit `cat /tmp/tests/test.status`'",
            timeout)

        # We give tar an explicit path to extract to guard against
        # the possibility of an arbitrary file overwrite attack if
        # anita is used to test an untrusted virtual machine.
        tarfile = open(scratch_disk_path, "r")
        subprocess.call(["tar", "xf", "-", "tests"],
                        cwd = self.workdir, stdin = tarfile)

	# For backwards compatibility, point workdir/atf to workdir/tests.
	compat_link = os.path.join(self.workdir, 'atf')
	if not os.path.lexists(compat_link):
	    os.symlink('tests', compat_link)

        return exit_status

    # Backwards compatibility
    run_atf_tests = run_tests

def console_interaction(child):
    # We need this in pexpect 2.x or everything will be printed twice
    child.logfile = None
    child.interact()

def login(child):
    child.send("\n")
    child.expect("login:")
    child.send("root\n")
    child.expect("\n# ")

def net_setup(child):
    child.send("dhclient ne2\n")
    child.expect("bound to.*\n# ")

def shell_cmd(child, cmd, timeout = -1):
    child.send("exec /bin/sh\n")
    child.expect("# ")
    child.send("PS1=" + distinctive_prompt_sh_string + "\n")
    child.expect(distinctive_prompt_re, timeout)    
    child.send(cmd + "\n")
    child.expect(distinctive_prompt_re, timeout)
    child.send("echo $?\n")
    child.expect("(\d+)")
    return int(child.match.group(1))

# Deprecated, use Anita.run_atf_tests
def test(child):
    login(child)
    # We go through some contortions here to return the meaningful exit status
    # from atf-run rather than the meaningless one from atf-report.
    return shell_cmd(child, "cd /usr/tests && " +
        "{ atf-run && :>/tmp/test.ok; } | atf-report && test -f /tmp/test.ok",
        7200)

#############################################################################
