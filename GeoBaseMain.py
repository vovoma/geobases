#!/usr/bin/python
# -*- coding: utf-8 -*-

"""
This module is a launcher for the GeoBases package.
"""

from sys import stdin, stderr, argv
import os
import os.path as op

import pkg_resources
from datetime import datetime
from math import ceil, log
from itertools import izip_longest, chain
from textwrap import dedent
import signal
import platform
import json
import re
import glob

import SimpleHTTPServer
import SocketServer

# Not in standard library
from termcolor import colored
import colorama
import argparse # in standard libraray for Python >= 2.7

# Private
from GeoBases import GeoBase, SOURCES_ADMIN

IS_WINDOWS = platform.system() in ('Windows',)

if not IS_WINDOWS:
    # On windows, SIGPIPE does not exist
    # Do not produce broken pipes when head and tail are used
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)


try:
    # readline is not available on every platform
    import readline

    def complete(text, state):
        """Activate autocomplete on raw_input.
        """
        return (glob.glob(text + '*') + [None])[state]

    readline.set_completer_delims(' \t\n;')
    readline.parse_and_bind("tab: complete")
    readline.set_completer(complete)


    def ask_input(prompt, prefill=''):
        """Custom default when asking for input.
        """
        readline.set_startup_hook(lambda: readline.insert_text(prefill))
        try:
            return raw_input(prompt)
        finally:
            readline.set_startup_hook()

except ImportError:

    def ask_input(prompt, prefill=''):
        """Fallback.
        """
        answer = raw_input('%s[%s] ' % (prompt, prefill))
        if answer:
            return answer
        else:
            # No answer, returning default
            return prefill


def is_in_path(command):
    """
    This checks if a command is in the PATH.
    """
    path = os.popen('which %s 2> /dev/null' % command, 'r').read()

    if path:
        return True
    else:
        return False


def get_stty_size():
    """
    This gives terminal size information using external
    command stty.
    This function is not great since where stdin is used, stty
    fails and we return the default case.
    """
    size = os.popen('stty size 2>/dev/null', 'r').read()

    if not size:
        return (80, 160)

    return tuple(int(d) for d in size.split())


def get_term_size():
    """
    This gives terminal size information.
    """
    try:
        import fcntl, termios, struct
    except ImportError:
        return get_stty_size()

    def ioctl_GWINSZ(fd):
        """Read terminal size."""
        try:
            cr = struct.unpack('hh', fcntl.ioctl(fd, termios.TIOCGWINSZ, '1234'))
        except IOError:
            return
        return cr

    env = os.environ
    cr  = ioctl_GWINSZ(0) or ioctl_GWINSZ(1) or ioctl_GWINSZ(2)

    if not cr:
        try:
            fd = os.open(os.ctermid(), os.O_RDONLY)
            cr = ioctl_GWINSZ(fd)
            os.close(fd)
        except IOError:
            pass

    if not cr:
        cr = env.get('LINES', 25), env.get('COLUMNS', 80)

    return int(cr[0]), int(cr[1])


class RotatingColors(object):
    """
    This class is used for generating alternate colors
    for the Linux output.
    """
    def __init__(self, background):

        if background == 'black':
            self._availables = [
                 ('cyan',  None,      []),
                 ('white', 'on_grey', []),
            ]

        elif background == 'white':
            self._availables = [
                 ('grey', None,       []),
                 ('blue', 'on_white', []),
            ]

        else:
            raise ValueError('Accepted background color: "black" or "white", not "%s".' % \
                             background)

        self._background = background
        self._current    = 0


    def next(self):
        """We increase the current color.
        """
        self._current += 1

        if self._current == len(self._availables):
            self._current = 0


    def get(self):
        """Get current color.
        """
        return self._availables[self._current]


    def convertRaw(self, col):
        """Get special raw color. Only change foreground color.
        """
        current    = list(col)
        current[0] = 'yellow' if self._background == 'black' else 'green'
        return tuple(current)


    def convertJoin(self, col):
        """Get special join color. Only change foreground color.
        """
        current    = list(col)
        current[0] = 'green' if self._background == 'black' else 'cyan'
        return tuple(current)


    @staticmethod
    def convertBold(col):
        """Get special field color. Only change bold type.
        """
        current    = list(col)
        current[2] = ['bold']
        return tuple(current)


    @staticmethod
    def getEmph():
        """Get special emphasized color.
        """
        return ('white', 'on_blue', [])


    @staticmethod
    def getHeader():
        """Get special header color.
        """
        return ('red', None, [])


    @staticmethod
    def getSpecial():
        """Get special field color.
        """
        return ('magenta', None, [])


def flatten(value, level=0):
    """Flatten nested structures into str.

    >>> flatten(())
    ''
    >>> flatten('T0')
    'T0'
    >>> flatten(['T1', 'T1'])
    'T1/T1'
    >>> flatten([('T2', 'T2'), 'T1'])
    'T2:T2/T1'
    >>> flatten([('T2', ['T3', 'T3']), 'T1'])
    'T2:T3,T3/T1'
    """
    splitters = ['/', ':', ',']

    if level >= len(splitters):
        splitter = '|'
    else:
        splitter = splitters[level]

    level += 1

    if isinstance(value, (list, tuple, set)):
        return splitter.join(flatten(e, level) for e in value)
    else:
        return str(value)


def check_ext_field(geob, field):
    """
    Check if a field given by user contains
    join fields and external field.
    """
    l = field.split(':', 1)

    if len(l) <= 1:
        return field, None

    f, ext_f = l

    if geob.hasJoin(f):
        return f, ext_f

    # In case of multiple join fields
    f = tuple(f.split(SPLIT))

    if geob.hasJoin(f):
        return f, ext_f

    return field, None


def fmt_ref(ref, ref_type, no_symb=False):
    """
    Display the reference depending on its type.
    """
    if ref_type == 'distance':
        if no_symb:
            return '%.3f' % ref
        return '%.2f km' % ref

    if ref_type == 'percentage':
        if no_symb:
            return '%.3f' % ref
        return '%.1f %%' % (100 * ref)

    if ref_type == 'phonemes':
        if isinstance(ref, (list, tuple, set)):
            return SPLIT.join(str(e) for e in ref)
        else:
            return str(ref)

    if ref_type == 'index':
        return '%s' % int(ref)

    raise ValueError('ref_type %s was not allowed' % ref_type)



def display(geob, list_of_things, shown_fields, ref_type, important):
    """
    Main display function in Linux terminal, with
    nice color and everything.
    """
    if not list_of_things:
        print 'No elements to display.'
        return

    # Different behaviour given number of results
    # We adapt the width between MIN_CHAR_COL and MAX_CHAR_COL
    # given number of columns and term width
    n   = len(list_of_things)
    lim = int(get_term_size()[1] / float(n + 1))
    lim = min(MAX_CHAR_COL, max(MIN_CHAR_COL, lim))

    if n == 1:
        # We do not truncate names if only one result
        truncate = None
    else:
        truncate = lim

    c = RotatingColors(BACKGROUND_COLOR)

    for f in shown_fields:
        # Computing clean fields, external fields, ...
        if f == REF:
            cf = REF
        else:
            cf, ext_f = check_ext_field(geob, f)
            if ext_f is None:
                get = lambda k: geob.get(k, cf)
            else:
                get = lambda k: geob.get(k, cf, ext_field=ext_f)

        if cf in important:
            col = c.getEmph()
        elif cf == REF:
            col = c.getHeader()
        elif str(cf).startswith('__'):
            col = c.getSpecial() # For special fields like __dup__
        else:
            col = c.get()

        if str(cf).endswith('@raw'):
            col = c.convertRaw(col)  # For @raw fields

        if geob.hasJoin(cf):
            col = c.convertJoin(col) # For joined fields

        # Fields on the left
        l = [fixed_width(f, c.convertBold(col), lim, truncate)]

        if f == REF:
            for h, _ in list_of_things:
                l.append(fixed_width(fmt_ref(h, ref_type), col, lim, truncate))
        else:
            for _, k in list_of_things:
                l.append(fixed_width(get(k), col, lim, truncate))

        next(c)
        print ''.join(l)


def fields_to_show(defaults, omit, show, show_additional):
    """Process fields to show.
    """
    if not show:
        show = [REF] + defaults[:]

    # Building final shown headers
    shown_fields = [f for f in show if f not in omit]

    # Trying to cleverly position addtional field
    positions = []
    for af in show_additional:
        for i, f in enumerate(shown_fields):
            if af.startswith(f):
                positions.append(i+1)
                break
        else:
            positions.append(-1)

    already_inserted = 0
    for af, p in zip(show_additional, positions):
        if p == -1:
            shown_fields.insert(-1, af)
        else:
            shown_fields.insert(p + already_inserted, af)
            already_inserted += 1

    return shown_fields



def display_quiet(geob, list_of_things, shown_fields, ref_type, delim, header):
    """
    This function displays the results in programming
    mode, with --quiet option. This is useful when you
    want to use use the result in a pipe for example.
    """
    # Headers joined
    j_headers = delim.join(str(f) for f in shown_fields)

    # Displaying headers only for RH et CH
    if header == 'RH':
        print j_headers
    elif header == 'CH':
        print '#%s' % j_headers

    # Caching getters
    getters = {}
    for f in shown_fields:
        if f == REF:
            continue
        cf, ext_f = check_ext_field(geob, f)
        if ext_f is None:
            getters[f] = cf, ext_f, lambda k, cf, ext_f: geob.get(k, cf)
        else:
            getters[f] = cf, ext_f, lambda k, cf, ext_f: geob.get(k, cf, ext_field=ext_f)

    for h, k in list_of_things:
        l = []
        for f in shown_fields:
            if f == REF:
                l.append(fmt_ref(h, ref_type, no_symb=True))
            else:
                # Get from getters cache
                cf, ext_f, get = getters[f]

                v = get(k, cf, ext_f)
                # Small workaround to display nicely lists in quiet mode
                # Fields @raw are already handled with raw version, but
                # __dup__ field has no raw version for dumping
                if isinstance(v, (list, tuple, set)):
                    l.append(flatten(v))
                else:
                    l.append(str(v))

        print delim.join(l)


def display_browser(templates, nb_res):
    """Display templates in the browser.
    """
    # We manually launch browser, unless we risk a crash
    to_be_launched = []

    for template in templates:
        if template.endswith('_table.html'):
            if nb_res <= TABLE_BROWSER_LIM:
                to_be_launched.append(template)
            else:
                print '/!\ "%s %s:%s/%s" not launched automatically. %s results, may be slow.' % \
                        (BROWSER, ADDRESS, PORT, template, nb_res)

        elif template.endswith('_map.html'):
            if nb_res <= MAP_BROWSER_LIM:
                to_be_launched.append(template)
            else:
                print '/!\ "%s %s:%s/%s" not launched automatically. %s results, may be slow.' % \
                        (BROWSER, ADDRESS, PORT, template, nb_res)
        else:
            to_be_launched.append(template)

    if to_be_launched:
        urls = ['%s:%s/%s' % (ADDRESS, PORT, tpl) for tpl in to_be_launched]
        os.system('%s %s &' % (BROWSER, ' '.join(urls)))



def launch_http_server(address, port):
    """Launch a SimpleHTTPServer.
    """
    class MyTCPServer(SocketServer.TCPServer):
        """Overrides standard library.
        """
        allow_reuse_address = True

    Handler = SimpleHTTPServer.SimpleHTTPRequestHandler
    httpd   = MyTCPServer((address, port), Handler)

    try:
        print '* Serving on %s:%s (hit ctrl+C to stop)' % (address, port)
        httpd.serve_forever()

    except KeyboardInterrupt:
        print '\n* Shutting down gracefully...'
        httpd.shutdown()
        print '* Done'



def fixed_width(s, col, lim=25, truncate=None):
    """
    This function is useful to display a string in the
    terminal with a fixed width. It is especially
    tricky with unicode strings containing accents.
    """
    if truncate is None:
        truncate = 1000

    printer = '%%-%ss' % lim # is something like '%-3s'

    # To truncate on the appropriate number of characters
    # We decode before truncating (so non-ascii characters
    # will be counted only once when using len())
    # Then we encode again before display
    ds = str(s).decode('utf8')                      # decode
    es = (printer % ds[0:truncate]).encode('utf8')  # encode

    if len(ds) > truncate:
        es = es[:-2] + '… '

    return colored(es, *col)


def scan_coords(u_input, geob, verbose):
    """
    This function tries to interpret the main
    argument as either coordinates (lat, lng) or
    a key like ORY.
    """
    # First we try input a direct key
    if u_input in geob:
        coords = geob.getLocation(u_input)

        if coords is None:
            error('geocode_unknown', u_input)

        return coords

    # Then we try input as geocode
    try:
        free_geo = u_input.strip('()')

        for char in '\\', '"', "'":
            free_geo = free_geo.replace(char, '')

        for sep in '^', ';', ',':
            free_geo = free_geo.replace(sep, ' ')

        coords = tuple(float(l) for l in free_geo.split())

    except ValueError:
        pass
    else:
        if len(coords) == 2        and \
           -90  <= coords[0] <= 90 and \
           -180 <= coords[1] <= 180:

            if verbose:
                print 'Geocode recognized: (%.3f, %.3f)' % coords

            return coords

        error('geocode_format', u_input)

    # All cases failed
    warn('key', u_input, geob.data, geob.loaded)
    exit(1)


def guess_delimiter(row):
    """Heuristic to guess the top level delimiter.
    """
    discarded  = set([
        '#', # this is for comments
        '_', # this is for spaces
        ' ', # spaces are not usually delimiter, unless we find no other
        '"', # this is for quoting
        '.', # this is for decimal numbers
        '@', # this is for duplicated keys
    ])
    candidates = set([l for l in row.rstrip() if not l.isalnum() and l not in discarded])
    counters   = dict((c, row.count(c)) for c in candidates)

    # Testing spaces from higher to lower, break on biggest match
    for alternate in [' ' * i for i in xrange(16, 3, -1)]:
        if row.count(alternate):
            counters[alternate] = row.count(alternate)
            break

    if counters:
        return max(counters.iteritems(), key=lambda x: x[1])[0]
    else:
        # In this case, we could not find any delimiter, we may
        # as well return ' '
        return ' '


def generate_headers(n):
    """Generate n headers.
    """
    for i in xrange(n):
        yield 'H%s' % i


ADD_INFO_REG = re.compile("([^{}]*)({?[^{}]*}?)({?[^{}]*}?)")

def clean_headers(headers):
    """
    Remove additional informations from headers,
    and return what was found.
    """
    subdelimiters = {}
    join          = []

    for i, h in enumerate(headers):

        m = ADD_INFO_REG.match(h)
        if m is None:
            continue

        clean_h, jn, subd = m.groups()
        headers[i] = clean_h

        # We consider the join only if the user did not give nothing or empty {}
        jn = jn.strip('{}')
        if jn:
            join.append({
                'fields' : clean_h,
                'with'   : jn.split(':', 1)
            })

        # For the subdelimiters we consider {} as empty string
        if subd:
            subd = subd.strip('{}')
            if subd == '':
                subdelimiters[clean_h] = ''
            else:
                subdelimiters[clean_h] = subd.split(':')

    return join, subdelimiters


def guess_headers(s_row):
    """Heuristic to guess the lat/lng fields from first row.
    """
    headers = list(generate_headers(len(s_row)))

    # Name candidates for lat/lng
    lat_candidates = set(['latitude',  'lat'])
    lng_candidates = set(['longitude', 'lng', 'lon'])

    lat_found, lng_found = False, False

    for i, f in enumerate(s_row):
        try:
            val = float(f)
        except ValueError:
            # Here the line was not a number, we check the name
            if f.lower() in lat_candidates and not lat_found:
                headers[i] = 'lat'
                lat_found  = True

            if f.lower() in lng_candidates and not lng_found:
                headers[i] = 'lng'
                lng_found  = True

        else:
            if val == int(val):
                # Round values are improbable as lat/lng
                continue

            if -90 <= val <= 90 and not lat_found:
                # possible latitude field
                headers[i] = 'lat'
                lat_found  = True

            elif -180 <= val <= 180 and not lng_found:
                # possible longitude field
                headers[i] = 'lng'
                lng_found  = True

    return headers


def score_key(v):
    """Eval likelihood of being a good field for generating keys.

    The shorter the better, and int get a len() of 1.
    0, 1 and floats are weird for key_fields, as well as 1-letter strings.
    """
    if str(v).endswith('__key__') or str(v).lower().endswith('id'):
        return 0

    if isinstance(v, float):
        return 1000

    if isinstance(v, int):
        if v <= 1: # we avoid a domain error on next case
            return 10
        return max(2, 25 / log(v))

    return len(v) if len(v) >= 2 else 10


def guess_key_fields(headers, s_row):
    """Heuristic to guess key_fields from headers and first row.
    """
    discarded  = set(['lat', 'lng'])
    candidates = []

    for h, v in zip(headers, s_row):
        # Skip discarded and empty values
        if h not in discarded and v:
            try:
                val = float(v)
            except ValueError:
                # is *not* a number
                candidates.append((h, score_key(v)))
            else:
                # is a number
                if val == int(val):
                    candidates.append((h, score_key(int(val))))
                else:
                    candidates.append((h, score_key(val)))

    if not candidates:
        return [headers[0]]

    return [ min(candidates, key=lambda x: x[1])[0] ]


def build_pairs(L, layout='v'):
    """
    Some formatting for help.
    """
    n = float(len(L))
    h = int(ceil(n / 2)) # half+

    if layout == 'h':
        return izip_longest(L[::2], L[1::2], fillvalue='')

    if layout == 'v':
        return izip_longest(L[:h], L[h:], fillvalue='')

    raise ValueError('Layout must be "h" or "v", but was "%s"' % layout)


def split_if_several(value):
    """Only split if several elements.
    """
    value = value.split(SPLIT)

    if len(value) == 1:
        return value[0]
    return value


def fmt_stuff(option, value):
    """Format stuff from the configuration file.
    """
    if option == 'delimiter':
        return str(value)

    if option == 'headers':
        return flatten(value)

    if option == 'key_fields':
        return flatten(value)

    if option == 'one_indices':
        return flatten(value)

    if option == 'one_join':
        if len(value['with']) < 2:
            if not value['with'][0]:
                return flatten(value['fields'])
            return '%s{%s}' % (flatten(value['fields']), value['with'][0])
        else:
            return '%s{%s:%s}' % (flatten(value['fields']), value['with'][0], flatten(value['with'][1]))

    raise ValueError('Did not understand option "%s".' % option)



def best_field(candidates, possibilities, default=None):
    """Select best candidate in possibilities.
    """
    for candidate in candidates:
        if candidate in possibilities:
            return candidate
    return default


def warn(name, *args):
    """
    Display a warning on stderr.
    """
    if name == 'key':
        print >> stderr, '/!\ Key %s was not in base, for data "%s" and source %s' % \
                (args[0], args[1], args[2])


def error(name, *args):
    """
    Display an error on stderr, then exit.
    First argument is the error type.
    """
    if name == 'trep_support':
        print >> stderr, '\n/!\ No opentrep support. Check if OpenTrepWrapper can import libpyopentrep.'

    elif name == 'geocode_support':
        print >> stderr, '\n/!\ No geocoding support for data type %s.' % args[0]

    elif name == 'data':
        print >> stderr, '\n/!\ Wrong data type "%s". You may select:' % args[0]
        for p in build_pairs(args[1]):
            print >> stderr, '\t%-20s\t%-20s' % p

    elif name == 'field':
        print >> stderr, '\n/!\ Wrong field "%s".' % args[0]
        print >> stderr, 'For data type "%s", you may select:' % args[1]
        for p in build_pairs(args[2]):
            print >> stderr, '\t%-20s\t%-20s' % p

    elif name == 'geocode_format':
        print >> stderr, '\n/!\ Bad geocode format: %s' % args[0]

    elif name == 'geocode_unknown':
        print >> stderr, '\n/!\ Geocode was unknown for %s' % args[0]

    elif name == 'empty_stdin':
        print >> stderr, '\n/!\ Stdin was empty'

    elif name == 'wrong_value':
        print >> stderr, '\n/!\ Wrong value "%s", should be in %s.' % (args[0], args[1])

    elif name == 'type':
        print >> stderr, '\n/!\ Wrong type for "%s", should be "%s".' % (args[0], args[1])

    elif name == 'aborting':
        print >> stderr, '\n/!\ %s' % args[0]

    exit(1)


#######
#
#  MAIN
#
#######

# Global defaults
PACKAGE_NAME = 'GeoBasesDev'
SCRIPT_NAME  = 'GeoBase'

DESCRIPTION  = 'Data services and visualization'
CONTACT_INFO = '''
Report bugs to    : geobases.dev@gmail.com
Home page         : <http://opentraveldata.github.com/geobases/>
API documentation : <https://geobases.readthedocs.org/>
Wiki pages        : <https://github.com/opentraveldata/geobases/wiki/_pages>
'''
HELP_SOURCES = SOURCES_ADMIN.build_status()
CLI_EXAMPLES = '''
* Command line examples

 $ %s ORY CDG                    # query on the keys ORY and CDG
 $ %s --closest CDG              # find closest from CDG
 $ %s --near '48.853, 2.348'     # find near some geocode
 $ %s --fuzzy "san francisko"    # fuzzy search, with typo ;)
 $ %s --help                     # your best friend
 $ cat data.csv | %s             # with your data
''' % ((op.basename(argv[0]),) * 6)

DEF_BASE            = 'ori_por'
DEF_FUZZY_LIMIT     = 0.85
DEF_NEAR_LIMIT      = 50.
DEF_CLOSEST_LIMIT   = 10
DEF_TREP_FORMAT     = 'S'
DEF_QUIET_DELIM     = '^'
DEF_QUIET_HEADER    = 'CH'
DEF_FUZZY_FIELDS    = ('name', 'country_name', 'currency_name', '__key__')
DEF_EXACT_FIELDS    = ('__key__',)
DEF_PHONETIC_FIELDS = ('name', 'country_name', 'currency_name', '__key__')
DEF_PHONETIC_METHOD = 'dmetaphone'
DEF_OMIT_FIELDS     = []
DEF_SHOW_FIELDS     = []
DEF_SHOW_ADD_FIELDS = []

# Magic value option to skip and leave default, or disable
SKIP    = '_'
SPLIT   = '/'
DISABLE = '__none__'
REF     = '__ref__'

# For requests with findWith, force stringification before testing
FORCE_STR = False

ALLOWED_ICON_TYPES       = (None, 'auto', 'S', 'B')
ALLOWED_INTER_TYPES      = ('__key__', '__exact__', '__fuzzy__', '__phonetic__')
ALLOWED_PHONETIC_METHODS = ('dmetaphone', 'dmetaphone-strict', 'metaphone', 'nysiis')
ALLOWED_COMMANDS         = ('status', 'fullstatus', 'drop', 'restore', 'edit')

DEF_INTER_FIELDS = ('iata_code', '__key__')
DEF_INTER_TYPE   = '__exact__'

# Considered truthy values for command line option
TRUTHY = ('1', 'Y')

# Duplicates handling in feed mode
DEF_DISCARD_RAW = 'F'
DEF_DISCARD     = False
DEF_INDICES     = []

DEF_JOIN_RAW = SKIP
DEF_JOIN     = []

# Port for SimpleHTTPServer
ADDRESS = '0.0.0.0'
PORT    = 8000
BROWSER = 'firefox'

if is_in_path('google-chrome'):
    BROWSER = 'google-chrome'

# Defaults for map
DEF_LABEL_FIELDS     = ('name',       'country_name', '__key__')
DEF_WEIGHT_FIELDS    = ('page_rank',  'population',   None)
DEF_COLOR_FIELDS     = ('raw_offset', 'fclass',       None)
DEF_ICON_TYPE        = 'auto' # icon type: small, big, auto, ...
DEF_LINK_DUPLICATES  = True
DEF_DRAW_JOIN_FIELDS = False

MAP_BROWSER_LIM   = 8000   # limit for launching browser automatically
TABLE_BROWSER_LIM = 2000   # limit for launching browser automatically

# Graph defaults, generate_headers is used for stdin input
DEF_GRAPH_WEIGHT = None
DEF_GRAPH_FIELDS = ('continent_name', 'raw_offset',
                    'alliance_code',  'unified_code') + \
                   tuple(generate_headers(5)) + \
                   ('__key__',)

# Terminal width defaults
DEF_CHAR_COL = 25
MIN_CHAR_COL = 3
MAX_CHAR_COL = 40
DEF_NUM_COL  = int(get_term_size()[1] / float(DEF_CHAR_COL)) - 1

ENV_WARNINGS = []

BACKGROUND_COLOR = os.getenv('BACKGROUND_COLOR', None) # 'white'

if BACKGROUND_COLOR not in ['black', 'white']:
    ENV_WARNINGS.append('''
    **********************************************************************
    $BACKGROUND_COLOR environment variable not properly set.             *
    Accepted values are 'black' and 'white'. Using default 'black' here. *
    To disable this message, add to your ~/.bashrc or ~/.zshrc:          *
                                                                         *
        export BACKGROUND_COLOR=black # or white
                                                                         *
    *************************************************************** README
    ''')

    BACKGROUND_COLOR = 'black'


if not is_in_path(SCRIPT_NAME):
    ENV_WARNINGS.append('''
    **********************************************************************
    "%s" does not seem to be in your $PATH.                         *
    To disable this message, add to your ~/.bashrc or ~/.zshrc:          *
                                                                         *
        export PATH=$PATH:$HOME/.local/bin
                                                                         *
    *************************************************************** README
    ''' % SCRIPT_NAME)


if ENV_WARNINGS:
    # Assume the user did not read the wiki :D
    ENV_WARNINGS.append('''
    **********************************************************************
    By the way, since you probably did not read the documentation :D,    *
    you should also add this for the completion to work with zsh.        *
    You are using zsh right o_O?                                          *
                                                                         *
        # Add custom completion scripts
        fpath=(~/.zsh/completion $fpath)
        autoload -U compinit
        compinit
                                                                         *
    *************************************************************** README
    ''')


def handle_args():
    """Command line parsing.
    """
    # or list formatter
    fmt_or = lambda L : ' or '.join('"%s"' % str(e) for e in L)

    parser = argparse.ArgumentParser(description=DESCRIPTION,
                                     formatter_class=argparse.RawTextHelpFormatter)

    parser.epilog = '%s\n%s\n%s' % (CLI_EXAMPLES, HELP_SOURCES, CONTACT_INFO)

    parser.add_argument('keys',
        help = dedent('''\
        Main argument. This will be used as a list of keys on which we
        apply filters. Leave empty to consider all keys.
        '''),
        metavar = 'KEY',
        nargs = '*')

    parser.add_argument('-A', '--admin',
        help = dedent('''\
        This option can be used to administrate sources.
        It accepts a two optional arguments: command, base.
        As command argument, you may use either
        %s.
        Leave empty and answer questions otherwise.
        ''' % ', '.join(ALLOWED_COMMANDS)),
        nargs = '*',
        metavar = 'COMMAND',
        default = None)

    parser.add_argument('-a', '--ask',
        help = dedent('''\
        This option turns on learning mode, where you just
        answer questions about what you want to do.
        '''),
        action = 'store_true')

    parser.add_argument('-b', '--base',
        help = dedent('''\
        Choose a different data type, default is "%s". Also available are
        stations, airports, countries... Give unadmissible value
        and all possibilities will be displayed.
        ''' % DEF_BASE),
        metavar = 'BASE',
        default = DEF_BASE)

    parser.add_argument('-f', '--fuzzy',
        help = dedent('''\
        Rather than looking up a key, this mode will search the best
        match for the field given by --fuzzy-field option, for
        the argument. Limit can be specified with --fuzzy-limit option.
        '''),
        metavar = 'VALUE',
        default = None,
        nargs = '+')

    parser.add_argument('-F', '--fuzzy-field',
        help = dedent('''\
        When performing a fuzzy search, specify the field to be chosen.
        Default is %s
        depending on fields.
        Give unadmissible field and available values will be displayed.
        ''' % fmt_or(DEF_FUZZY_FIELDS)),
        metavar = 'FIELD',
        default = None)

    parser.add_argument('-L', '--fuzzy-limit',
        help = dedent('''\
        Specify a min limit for fuzzy searches, default is %s.
        This is the Levenshtein ratio of the two strings.
        ''' % DEF_FUZZY_LIMIT),
        metavar = 'RATIO',
        default = DEF_FUZZY_LIMIT,
        type = float)

    parser.add_argument('-p', '--phonetic',
        help = dedent('''\
        Rather than looking up a key, this mode will search the best phonetic
        match for the field given by --phonetic-field option, for
        the argument. This works well only for english.
        Use --phonetic-method to change the method used.
        '''),
        metavar = 'VALUE',
        default = None,
        nargs = '+')

    parser.add_argument('-P', '--phonetic-field',
        help = dedent('''\
        When performing a phonetic search, specify the field to be chosen.
        Default is %s
        depending on fields.
        Give unadmissible field and available values will be displayed.
        ''' % fmt_or(DEF_PHONETIC_FIELDS)),
        metavar = 'FIELD',
        default = None)

    parser.add_argument('-y', '--phonetic-method',
        help = dedent('''\
        By default, --phonetic uses "%s" method. With this option, you
        can change this to %s.
        ''' % (DEF_PHONETIC_METHOD, fmt_or(ALLOWED_PHONETIC_METHODS))),
        metavar = 'METHOD',
        default = DEF_PHONETIC_METHOD)

    parser.add_argument('-e', '--exact',
        help = dedent('''\
        Rather than looking up a key, this mode will search all keys
        whose specific field given by --exact-field match the
        argument. By default, the %s field is used for the search.
        You can have several field matching by giving multiple values
        delimited by "%s" for --exact-field. Make sure you give the
        same number of values delimited also by "%s" then.
        ''' % (fmt_or(DEF_EXACT_FIELDS), SPLIT, SPLIT)),
        metavar = 'VALUE',
        default = None,
        nargs = '+')

    parser.add_argument('-E', '--exact-field',
        help = dedent('''\
        When performing an exact search, specify the field to be chosen.
        Default is %s. Give unadmissible field and available
        values will be displayed.
        You can give multiple fields delimited by "%s". Make sure
        you give the same number of values delimited also by "%s" for -e then.
        ''' % (fmt_or(DEF_EXACT_FIELDS), SPLIT, SPLIT)),
        metavar = 'FIELD',
        default = None)

    parser.add_argument('-r', '--reverse',
        help = dedent('''\
        When possible, reverse the logic of the filter. Currently
        only --exact supports that.
        '''),
        action = 'store_true')

    parser.add_argument('-O', '--or',
        help = dedent('''\
        By default, --exact multiple searches are combined with *and*,
        passing this option will change that to a *or*.
        '''),
        action = 'store_true')

    parser.add_argument('-n', '--near',
        help = dedent('''\
        Rather than looking up a key, this mode will search the entries
        close to a geocode or a key. Radius is given by --near-limit
        option, and geocode is given as main argument. If you wish to give
        a geocode as input, use the 'lat, lng' format, with quotes.
        Example: -n CDG
        '''),
        metavar = 'VALUE',
        default = None,
        nargs = '+')

    parser.add_argument('-N', '--near-limit',
        help = dedent('''\
        Specify a radius in km when performing geographical
        searches with --near. Default is %s km.
        ''' % DEF_NEAR_LIMIT),
        metavar = 'RADIUS',
        default = DEF_NEAR_LIMIT,
        type = float)

    parser.add_argument('-c', '--closest',
        help = dedent('''\
        Rather than looking up a key, this mode will search the closest entries
        from a geocode or a key. Number of results is limited by --closest-limit
        option, and geocode is given as main argument. If you wish to give
        a geocode as input, use the 'lat, lng' format, with quotes.
        Example: -c '48.853, 2.348'
        '''),
        metavar = 'VALUE',
        default = None,
        nargs = '+')

    parser.add_argument('-C', '--closest-limit',
        help = dedent('''\
        Specify a limit for closest search with --closest, default is %s.
        ''' % DEF_CLOSEST_LIMIT),
        metavar = 'LIM',
        default = DEF_CLOSEST_LIMIT,
        type = int)

    parser.add_argument('-d', '--disable-grid',
        help = dedent('''\
        When performing a geographical search, a geographical index is used.
        This may lead to inaccurate results in some (rare) case when using
        --closest searches (--near searches are never impacted).
        Adding this option will disable the index, and browse the full
        data set to look for the results.
        '''),
        action = 'store_true')

    parser.add_argument('-t', '--trep',
        help = dedent('''\
        Rather than looking up a key, this mode will use opentrep.
        '''),
        metavar = 'VALUE',
        default = None,
        nargs = '+')

    parser.add_argument('-T', '--trep-format',
        help = dedent('''\
        Specify a format for trep searches with --trep, default is "%s".
        ''' % DEF_TREP_FORMAT),
        metavar = 'FORMAT',
        default = DEF_TREP_FORMAT)

    parser.add_argument('-o', '--omit',
        help = dedent('''\
        Does not print some fields on stdout.
        May help to get cleaner output.
        "%s" is an available value as well as any other fields.
        ''' % REF),
        metavar = 'FIELD',
        nargs = '+',
        default = DEF_OMIT_FIELDS)

    parser.add_argument('-s', '--show',
        help = dedent('''\
        Only print some fields on stdout.
        May help to get cleaner output.
        "%s" is an available value as well as any other fields.
        ''' % REF),
        metavar = 'FIELD',
        nargs = '+',
        default = DEF_SHOW_FIELDS)

    parser.add_argument('-S', '--show-additional',
        help = dedent('''\
        In addition to the normal displayed fields, add other fields.
        This is useful for displaying fields with join information,
        with the field:external_field syntax.
        '''),
        metavar = 'FIELD',
        nargs = '+',
        default = DEF_SHOW_ADD_FIELDS)

    parser.add_argument('-l', '--limit',
        help = dedent('''\
        Specify a limit for the number of results.
        This must be an integer.
        Default is %s, except in quiet mode where it is disabled.
        ''' % DEF_NUM_COL),
        metavar = 'NUM',
        default = None)

    parser.add_argument('-i', '--indexation',
        help = dedent('''\
        Specify metadata, for stdin input as well as existing bases.
        This will override defaults for existing bases.
        6 optional arguments: delimiter, headers, key_fields, discard_dups, indices, join.
            1) default delimiter is smart :).
            2) default headers will use numbers, and try to sniff lat/lng.
               Use __head__ as header value to
               burn the first line to define the headers.
               Use header{base:external_field}{subdelimiter} syntax to define
               a join clause for a header, and/or a subdelimiter.
               To give just the subdelimiter you may use header{}{subdelimiter}.
            3) default key_fields will take the first plausible field.
               Put %s to use None as key_fields, which will cause the keys
               to be generated from the line numbers.
            4) discard_dups is a boolean to toggle duplicated keys dropping.
               Put %s as a truthy value, any other value will be falsy.
            5) indices is a field, if given, this will build an index on that field
               to speed up findWith queries.
            6) join is a join clause defined with fields{base:external_fields}.
               This clause can concern multiple fields delimited by "%s".
        Multiple fields may be specified with "%s" delimiter.
        For any field, you may put "%s" to leave the default value.
        Example: -i ',' key/name/country key/country _
        ''' % (DISABLE, fmt_or(TRUTHY), SPLIT, SPLIT, SKIP)),
        nargs = '+',
        metavar = 'OPTION',
        default = [])

    parser.add_argument('-I', '--interactive-query',
        help = dedent('''\
        If given, this option will consider stdin
        input as query material, not data for loading.
        It will read values line by line, and perform a search on them.
        2 optional arguments: field, type.
            1) field is the field from which the data is supposed to be.
               Default is %s depending on fields.
            2) type is the type of matching, either
               %s.
               Default is "%s".
               __key__ type means we will perform a direct key access.
               For fuzzy searches, default ratio is set to %s,
               but can be changed with --fuzzy-limit.
               For phonetic searches, default method is %s,
               but can be changed with --phonetic-method.
        For any field, you may put "%s" to leave the default value.
        Example: -I icao_code __fuzzy__
        ''' % (fmt_or(DEF_INTER_FIELDS), fmt_or(ALLOWED_INTER_TYPES),
               DEF_INTER_TYPE, DEF_FUZZY_LIMIT, DEF_PHONETIC_METHOD, SKIP)),
        nargs = '*',
        metavar = 'OPTION',
        default = None)

    parser.add_argument('-q', '--quiet',
        help = dedent('''\
        Turn off verbosity and provide a programmer friendly output.
        This is a csv-like output, and may still be combined with
        --omit and --show. Configure with --quiet-options.
        '''),
        action = 'store_true')

    parser.add_argument('-Q', '--quiet-options',
        help = dedent('''\
        Custom the quiet mode.
        2 optional arguments: delimiter, header.
            1) default delimiter is "%s".
            2) the second parameter is used to control
               header display:
                   - RH to add a raw header,
                   - CH to add a commented header,
                   - any other value will not display the header.
               Default is "%s".
        For any field, you may put "%s" to leave the default value.
        Example: -Q ';' RH
        ''' % (DEF_QUIET_DELIM, DEF_QUIET_HEADER, SKIP)),
        nargs = '+',
        metavar = 'OPTION',
        default = [])

    parser.add_argument('-m', '--map',
        help = dedent('''\
        This is the map display.
        Configure with --map-options.
        HTML/Javascript/JSON files are generated.
        Unless --quiet is also set, a browser will be launched
        and a simple HTTP server will serve the HTML results
        on %s:%s.
        ''' % (ADDRESS, PORT)),
        action = 'store_true')

    parser.add_argument('-M', '--map-options',
        help = dedent('''\
        6 optional arguments: label, weight, color, icon, duplicates, draw_join_fields.
            1) label is the field to display on map points.
               Default is %s depending on fields.
            2) weight is the field used to draw circles around points.
               Default is %s depending on fields.
               Put "%s" to disable circles.
            3) color is the field use to color icons.
               Default is %s depending on fields.
               Put "%s" to disable coloring.
            4) icon is the icon type, either:
                   - "B" for big,
                   - "S" for small,
                   - "auto" for automatic,
                   - "%s" to disable icons.
               Default is "%s".
            5) duplicates is a boolean to toggle lines between duplicated keys.
               Default is %s. Put %s as a truthy value,
               any other value will be falsy.
            6) draw_join_fields is a boolean to toggle lines drawing when
               scanning joined fields who may have geocoding information.
               Default is %s. Put %s as a truthy value,
               any other value will be falsy.
        For any field, you may put "%s" to leave the default value.
        Example: -M _ population _ __none__ _
        ''' % ((fmt_or(DEF_LABEL_FIELDS), fmt_or(DEF_WEIGHT_FIELDS), DISABLE,
                fmt_or(DEF_COLOR_FIELDS), DISABLE, DISABLE, DEF_ICON_TYPE,
                DEF_LINK_DUPLICATES, fmt_or(TRUTHY), DEF_DRAW_JOIN_FIELDS,
                fmt_or(TRUTHY), SKIP))),
        nargs = '+',
        metavar = 'OPTION',
        default = [])

    parser.add_argument('-g', '--graph',
        help = dedent('''\
        This is the graph display.
        Configure with --graph-fields.
        HTML/Javascript/JSON files are generated.
        Unless --quiet is also set, a browser will be launched
        and a simple HTTP server will serve the HTML results
        on %s:%s.
        ''' % (ADDRESS, PORT)),
        action = 'store_true')

    parser.add_argument('-G', '--graph-fields',
        help = dedent('''\
        This option has n arguments: fields used to build
        the graph display. Nodes are the field values. Edges
        represent the data.
        Defaults are, depending on fields, picked in
        %s [...].
        ''' % ', '.join(DEF_GRAPH_FIELDS[0:5])),
        nargs = '+',
        metavar = 'FIELD',
        default = [])

    parser.add_argument('-W', '--graph-weight',
        help = dedent('''\
        This option defines the field used to compute weights
        when drawing graphs. Put "%s" (which will be None) not
        to use any fields, but just count the number of lines.
        Default is "%s".
        ''' % (DISABLE, DEF_GRAPH_WEIGHT)),
        metavar = 'FIELD',
        default = DEF_GRAPH_WEIGHT)

    parser.add_argument('-w', '--with-types',
        help = dedent('''\
        When drawing graphs, consider values from different
        fields to be of different types. Concretely, if there
        are no types, this means we will create only one node
        if the same value is found accross different fields.
        With this option turned on, we would create different
        nodes.
        '''),
        action = 'store_true')

    parser.add_argument('-v', '--verbose',
        help = dedent('''\
        Provides additional informations:
            * warnings during data loading and queries
            * timing information for profiling
            * hints on how to make a data source permanent
            * probably other things
        '''),
        action = 'store_true')

    parser.add_argument('-u', '--update',
        help = dedent('''\
        If this option is set, instead of anything,
        the script will try to update the data files.
        Differences will be shown and the user has to answer
        'Y' or 'N' for each file.
        '''),
        action = 'store_true')

    parser.add_argument('-U', '--update-forced',
        help = dedent('''\
        If this option is set, instead of anything,
        the script will force the update of all data files.
        '''),
        action = 'store_true')

    parser.add_argument('-V', '--version',
        help = dedent('''\
        Display version information.
        '''),
        action = 'store_true')

    return vars(parser.parse_args())


def admin_mode(admin):
    """Handle admin commands.
    """
    help_ = dedent("""\
    ---------------------------------------------------------------
    (*) status     : display short data source status
    (*) fullstatus : display full data source configuration
    (*) drop       : drop all information for one data source
    (*) restore    : factory reset of all data sources information
    (*) edit       : add or edit an existing data source
    ------------------------------------------------------- SUMMARY
    """)

    if len(admin) < 1:
        print help_
        msg = '[ 0 ] Command (%s): ' % SPLIT.join(ALLOWED_COMMANDS)
        admin_command = ask_input(msg)

        while not admin_command:
            admin_command = ask_input(msg)
        admin.append(admin_command)

    if admin[0] not in ALLOWED_COMMANDS:
        error('wrong_value', admin[0], ALLOWED_COMMANDS)

    if admin[0] == 'restore':
        SOURCES_ADMIN.restore()
        return

    if len(admin) < 2:
        print '\n'.join(sorted(SOURCES_ADMIN))

        source_name = ask_input('[ 1 ] Source name : ')
        admin.append(source_name)

    if admin[1] in ('', '*'):
        admin[1] = None

    if admin[0] == 'status':
        print SOURCES_ADMIN.build_status(admin[1])

    elif admin[0] == 'fullstatus':
        SOURCES_ADMIN.full_status(admin[1])

    elif admin[0] == 'drop':
        source_name = ''
        while not source_name:
            print '/!\ Cannot be all sources'
            source_name = ask_input('[ 1 ] Source name : ')

        SOURCES_ADMIN.drop(source_name)
        SOURCES_ADMIN.save()

    elif admin[0] == 'edit':

        if admin[1] is not None:
            source_name = admin[1]
        else:
            source_name = ''
            while not source_name:
                print '/!\ Cannot be all sources'
                source_name = ask_input('[ 1 ] Source name : ')


        if source_name not in SOURCES_ADMIN:
            print '----- New source!'
            SOURCES_ADMIN.add(source_name, {
                'local' : False
            })

        # We will non empty values here
        conf = SOURCES_ADMIN.get(source_name)
        if conf is None:
            conf = {}
        new_conf = {}


        def_paths = conf.get('paths', [])
        if not def_paths:
            def_paths = [{ 'file' : '' }]
        else:
            def_paths = SOURCES_ADMIN.convert_paths_format(def_paths,
                                                           local=conf.get('local', True))

        new_conf['paths'] = []

        def_delimiter  = conf.get('delimiter',  '^')
        def_headers    = conf.get('headers',    [])
        def_key_fields = conf.get('key_fields', [])

        for path in def_paths:
            ref_path = dict(path.items())
            path = ask_input('[2/8] Paths       : ', path['file'])
            path = SOURCES_ADMIN.convert_paths_format(path,
                                                      local=conf.get('local', False))[0]

            if path['file'].endswith('.zip'):
                extract = ask_input('[   ] Which file in archive? ', ref_path.get('extract', ''))
                path['extract'] = extract

            if not path['file']:
                # Empty path mean we want to delete it
                continue

            if not path['file'].startswith('http://') and \
               not path['file'].startswith('https://'):
                # For local paths we propose copy in cache dir
                path['file'] = op.realpath(path['file'])

                if 'extract' in path:
                    # We propose to store the root archive in cache
                    if path['file'] != op.join(SOURCES_ADMIN.cache_dir, op.basename(path['file'])):
                        # Is the path file in the cache dir?
                        copy_in_cache = ask_input('[   ] Copy %s in %s and use from there [Y/N]? ' % \
                                                  (op.basename(path['file']), SOURCES_ADMIN.cache_dir), 'Y')

                        if copy_in_cache == 'Y':
                            _, copied = SOURCES_ADMIN.copy_in_cache(path['file'])
                            path['file'] = op.realpath(copied)

            # We propose for tmp files to be used as primary sources
            filename = SOURCES_ADMIN.handle_path(path, verbose=True)

            if path['file'] != op.join(SOURCES_ADMIN.cache_dir, op.basename(filename)):
                # Is the path file in the cache dir?
                copy_in_cache = ask_input('[   ] Copy %s in %s and use from there [Y/N]? ' % \
                                          (op.basename(filename), SOURCES_ADMIN.cache_dir), 'Y')

                if copy_in_cache == 'Y':
                    _, copied = SOURCES_ADMIN.copy_in_cache(filename)
                    path['file'] = op.realpath(copied)

            new_conf['paths'].append(path)

            if path == ref_path:
                # No need to download and check the first lines for known files
                continue

            with open(filename) as fl:
                first_l = fl.next().rstrip()

            print '\n===== First line: "%s"\n' % first_l
            def_delimiter  = guess_delimiter(first_l)
            def_headers    = guess_headers(first_l.split(def_delimiter))
            def_key_fields = guess_key_fields(def_headers, first_l.split(def_delimiter))


        delimiter = ask_input('[3/8] Delimiter   : ', fmt_stuff('delimiter', def_delimiter))
        if delimiter:
            new_conf['delimiter'] = delimiter


        headers = ask_input('[4/8] Headers     : ', fmt_stuff('headers', def_headers))
        if not headers:
            headers = []
        else:
            headers = headers.split(SPLIT)

        if headers:
            join, subdelimiters = clean_headers(headers)
            new_conf['headers'] = headers
            if join:
                new_conf['join'] = join
                print '----- Detected join %s' % str(join)
            if subdelimiters:
                new_conf['subdelimiters'] = subdelimiters
                print '----- Detected subdelimiters %s' % str(subdelimiters)


        key_fields = ask_input('[5/8] Key fields  : ', fmt_stuff('key_fields', def_key_fields))
        if key_fields:
            key_fields = split_if_several(key_fields)
            new_conf['key_fields'] = key_fields


        def_indices = conf.get('indices', [])
        if not def_indices:
            def_indices = ['']
        new_conf['indices'] = []

        for ind in def_indices:
            indices = ask_input('[6/8] Indices     : ', fmt_stuff('one_indices', ind))
            if indices:
                indices = split_if_several(indices)
                new_conf['indices'].append(indices)


        def_join = conf.get('join', [])
        if not def_join:
            def_join = [{
                'fields' : [],
                'with'   : ['']
            }]
        new_conf['join'] = []

        for j in def_join:
            m_join = ask_input('[7/8] Join        : ', fmt_stuff('one_join', j))
            m_join = clean_headers(m_join.split(SPLIT))[0]

            if m_join:
                m_join[0]['fields'] = split_if_several(m_join[0]['fields'])

                if len(m_join[0]['with']) > 1:
                    m_join[0]['with'][1] = split_if_several(m_join[0]['with'][1])

                new_conf['join'].extend(m_join)

        # Removing non-changes
        old_conf = {}
        for option, config in new_conf.items():
            if option in conf:
                if config == conf[option]:
                    del new_conf[option]
                else:
                    old_conf[option] = conf[option]

        if not new_conf:
            print '\n===== No changes'
        else:
            if old_conf:
                print '\n----- [changes: before]'
                print SOURCES_ADMIN.convert(old_conf)

            print '\n+++++ [changes: after]'
            print SOURCES_ADMIN.convert(new_conf)

            confirm = ask_input('[8/8] Confirm [Y/N]? ', 'Y')

            if confirm == 'Y':
                SOURCES_ADMIN.update(source_name, new_conf)
                SOURCES_ADMIN.save()
                print '\n===== Changes saved to %s' % SOURCES_ADMIN.sources_conf_path
            else:
                print '\n===== Aborted'


def ask_mode():
    """Learning mode.
    """

    print dedent("""\
    -----------------------------------------------------
                   WELCOME TO LEARNING MODE

                You will be guided through the
               possibilities by answering a few
                         questions.
    -----------------------------------------------------
    """)

    # 1. Choose base
    allowed = sorted(SOURCES_ADMIN)
    print '\n'.join(allowed)

    base = ask_input('[1/5] Which data source do you want to work with? ').strip()
    while base not in allowed:
        base = ask_input('[1/5] Which data source do you want to work with? ').strip()

    # 2. Choose from keys
    all_keys = ask_input('[2/5] Consider all data for this source [Y/N]? ', 'Y').strip()

    if all_keys == 'Y':
        from_keys = None
    else:
        from_keys = ask_input('[   ] Which keys should we consider (separated with " ")? ').strip()
        from_keys = from_keys.split()

    # 3. Choose search type
    allowed = ['none', 'exact', 'fuzzy', 'phonetic', 'near', 'closest']
    print '\n'.join(allowed)

    search = ask_input('[3/5] What kind of filter? ').strip()
    while search not in allowed:
        search = ask_input('[3/5] What kind of filter? ').strip()

    if search.strip().lower() in ('none',):
        search = None

    # 4. Search parameters
    field, value, limit = None, None, None

    if search in ['exact', 'fuzzy', 'phonetic']:
        allowed = sorted(SOURCES_ADMIN.get(base)['headers'])
        print '\n'.join(allowed)

        field = ask_input('[4/5] On which field? ')
        while field not in allowed:
            field = ask_input('[4/5] On which field? ').strip()

        value = ask_input('[   ] Which value to look for? ').strip()

    elif search in ['near', 'closest']:
        value = ask_input('[4/5] From which point (key or geocode)? ').strip()
        limit = ask_input('[   ] Which limit for the search (kms or number)? ').strip()

    # 5. Frontend
    allowed = ['terminal', 'quiet', 'map', 'graph']
    print '\n'.join(allowed)

    frontend = ask_input('[5/5] Which display? ').strip()
    while frontend not in allowed:
        frontend = ask_input('[5/5] Which display? ').strip()

    # 6. Conclusion
    parameters = {
        'base'      : base,
        'from_keys' : from_keys,
        'search'    : search,
        'field'     : field,
        'limit'     : limit,
        'value'     : value,
        'frontend'  : frontend
    }

    print
    print '-----------------------------------------------------'
    print
    print '              Congrats! You choosed:                 '
    print 
    for k, v in parameters.iteritems():
        print '(*) %-10s => %-10s' % (str(k), str(v))

    # One liner
    print
    print '      Equivalent one-liner command (run it !):       '
    print

    base_part         = '--base %s' % base
    from_keys_part    = '' if from_keys is None else ' '.join(from_keys)
    search_part       = ('--%s "%s"' % (search, value)) if search is not None else ''
    frontend_part     = ('--%s' % frontend) if frontend != 'terminal' else ''

    if search in ['exact', 'fuzzy', 'phonetic']:
        search_field_part = '--%s-field %s' % (search, field)
    elif search in ['near', 'closest']:
        search_field_part = '--%s-limit %s' % (search, limit)
    else:
        search_field_part = ''

    print '%s' % ' '.join(e for e in [SCRIPT_NAME,
                                      from_keys_part,
                                      base_part,
                                      search_field_part,
                                      search_part,
                                      frontend_part] if e)
    print '-----------------------------------------------------'

    return parameters



# How to profile: execute this and uncomment @profile
# $ kernprof.py --line-by-line --view file.py ORY
#@profile
def main():
    """
    Arguments handling.
    """
    # Filter colored signals on terminals.
    # Necessary for Windows CMD
    colorama.init()

    #
    # COMMAND LINE MANAGEMENT
    args = handle_args()


    #
    # ARGUMENTS
    #
    with_grid = not args['disable_grid']
    verbose   = not args['quiet']
    logorrhea = args['verbose']

    # Defining frontend
    if args['map']:
        frontend = 'map'
    elif args['graph']:
        frontend = 'graph'
    elif args['quiet']:
        frontend = 'quiet'
    else:
        frontend = 'terminal'

    if args['limit'] is None:
        # Limit was not set by user
        if frontend == 'terminal':
            limit = DEF_NUM_COL
        else:
            limit = None

    else:
        try:
            limit = int(args['limit'])
        except ValueError:
            error('type', args['limit'], 'int')

    # Interactive query?
    interactive_query_mode = args['interactive_query'] is not None


    #
    # CREATION
    #
    if logorrhea:
        before_init = datetime.now()

    if args['version']:
        r = pkg_resources.require(PACKAGE_NAME)[0]
        print 'Project  : %s' % r.project_name
        print 'Version  : %s' % r.version
        print 'Egg name : %s' % r.egg_name()
        print 'Location : %s' % r.location
        print 'Requires : %s' % ', '.join(str(e) for e in r.requires())
        print 'Extras   : %s' % ', '.join(str(e) for e in r.extras)
        exit(0)

    if args['base'] not in SOURCES_ADMIN:
        error('data', args['base'], sorted(SOURCES_ADMIN))

    # Updating file
    if args['update']:
        SOURCES_ADMIN.check_data_updates()
        exit(0)

    if args['update_forced']:
        SOURCES_ADMIN.check_data_updates(force=True)
        exit(0)


    if args['admin'] is not None:
        try:
            admin_mode(args['admin'])
        except (KeyboardInterrupt, EOFError):
            error('aborting', 'Aborting, changes will not be saved.')
        else:
            exit(0)


    if args['ask']:
        try:
            _ = ask_mode()
        except (KeyboardInterrupt, EOFError):
            error('aborting', 'Ending learning session :S.')
        else:
            exit(0)


    if not stdin.isatty() and not interactive_query_mode:
        try:
            first_l = stdin.next()
        except StopIteration:
            error('empty_stdin')

        source  = chain([first_l], stdin)
        first_l = first_l.rstrip() # For sniffers, we rstrip

        delimiter  = guess_delimiter(first_l)
        headers    = guess_headers(first_l.split(delimiter))
        key_fields = guess_key_fields(headers, first_l.split(delimiter))

        headers_r     = None # to store raw headers given
        subdelimiters = {}
        join          = []

        discard_dups_r = DEF_DISCARD_RAW
        discard_dups   = DEF_DISCARD
        indices        = DEF_INDICES

        m_join_r = DEF_JOIN_RAW
        m_join   = DEF_JOIN

        if len(args['indexation']) >= 1 and args['indexation'][0] != SKIP:
            delimiter = args['indexation'][0]

        if len(args['indexation']) >= 2 and args['indexation'][1] != SKIP:
            if args['indexation'][1] == '__head__':
                headers = source.next().rstrip().split(delimiter)
            else:
                headers   = args['indexation'][1].split(SPLIT)
                headers_r = headers[:] # backup
                l_join, l_subdelimiters = clean_headers(headers)
                join.extend(l_join)
                subdelimiters.update(l_subdelimiters)
        else:
            # Reprocessing the headers with custom delimiter
            headers = guess_headers(first_l.split(delimiter))

        if len(args['indexation']) >= 3 and args['indexation'][2] != SKIP:
            key_fields = None if args['indexation'][2] == DISABLE else args['indexation'][2].split(SPLIT)
        else:
            # Reprocessing the key_fields with custom headers
            key_fields = guess_key_fields(headers, first_l.split(delimiter))

        if len(args['indexation']) >= 4 and args['indexation'][3] != SKIP:
            discard_dups_r = args['indexation'][3]
            discard_dups   = discard_dups_r in TRUTHY

        if len(args['indexation']) >= 5 and args['indexation'][4] != SKIP:
            indices = [] if args['indexation'][4] == DISABLE else [args['indexation'][4].split(SPLIT)]

        if len(args['indexation']) >= 6 and args['indexation'][5] != SKIP:
            m_join_r = args['indexation'][5]
            m_join   = [] if args['indexation'][5] == DISABLE else clean_headers([args['indexation'][5]])[0]

            if m_join:
                m_join[0]['fields'] = tuple(m_join[0]['fields'].split(SPLIT))

                if len(m_join[0]['with']) > 1:
                    m_join[0]['with'][1] = tuple(m_join[0]['with'][1].split(SPLIT))

                join.extend(m_join)

        # Checking join bases
        for e in join:
            if e['with'][0] not in SOURCES_ADMIN:
                error('data', e['with'][0], sorted(SOURCES_ADMIN))

        if verbose:
            print 'Loading from stdin with [sniffed] option: -i "%s" "%s" "%s" "%s" "%s" "%s"' % \
                    (delimiter,
                     SPLIT.join(headers if headers_r is None else headers_r),
                     SPLIT.join(key_fields) if key_fields is not None else DISABLE,
                     discard_dups_r,
                     SPLIT.join(indices[0]) if indices else DISABLE,
                     m_join_r)

        options = {
            'source'       : source,
            'delimiter'    : delimiter,
            'headers'      : headers,
            'key_fields'   : key_fields,
            'discard_dups' : discard_dups,
            'indices'      : indices,
            'subdelimiters': subdelimiters,
            'join'         : join,
            'verbose'      : logorrhea
        }

        g = GeoBase(data='feed', **options)

        if logorrhea:
            SOURCES_ADMIN.help_permanent_add(options)

    else:
        # -i options overrides default
        add_options = {}

        if len(args['indexation']) >= 1 and args['indexation'][0] != SKIP:
            add_options['delimiter'] = args['indexation'][0]

        if len(args['indexation']) >= 2 and args['indexation'][1] != SKIP:
            add_options['headers'] = args['indexation'][1].split(SPLIT)
            l_join, l_subdelimiters = clean_headers(add_options['headers'])

            if l_join:
                add_options['join'] = l_join
            if l_subdelimiters:
                add_options['subdelimiters'] = l_subdelimiters

        if len(args['indexation']) >= 3 and args['indexation'][2] != SKIP:
            add_options['key_fields'] = None if args['indexation'][2] == DISABLE else args['indexation'][2].split(SPLIT)

        if len(args['indexation']) >= 4 and args['indexation'][3] != SKIP:
            add_options['discard_dups'] = args['indexation'][3] in TRUTHY

        if len(args['indexation']) >= 5 and args['indexation'][4] != SKIP:
            add_options['indices'] = [] if args['indexation'][4] == DISABLE else [args['indexation'][4].split(SPLIT)]
        if len(args['indexation']) >= 6 and args['indexation'][5] != SKIP:
            m_join = [] if args['indexation'][5] == DISABLE else clean_headers([args['indexation'][5]])[0]

            if m_join:
                m_join[0]['fields'] = tuple(m_join[0]['fields'].split(SPLIT))

                if len(m_join[0]['with']) > 1:
                    m_join[0]['with'][1] = tuple(m_join[0]['with'][1].split(SPLIT))

                if 'join' in add_options:
                    add_options['join'].extend(m_join)
                else:
                    add_options['join'] = m_join

        if verbose:
            if not add_options:
                print 'Loading "%s"...' % args['base']
            else:
                print 'Loading "%s" with custom: %s ...' % \
                        (args['base'], ' ; '.join('%s = %s' % kv for kv in add_options.items()))

        g = GeoBase(data=args['base'], verbose=logorrhea, **add_options)

    if logorrhea:
        after_init = datetime.now()

    # Tuning parameters
    if args['exact_field'] is None or args['exact_field'] == SKIP:
        args['exact_field'] = best_field(DEF_EXACT_FIELDS, g.fields)

    exact_fields = args['exact_field'].split(SPLIT)

    if args['fuzzy_field'] is None or args['fuzzy_field'] == SKIP:
        args['fuzzy_field'] = best_field(DEF_FUZZY_FIELDS, g.fields)

    if args['phonetic_field'] is None or args['phonetic_field'] == SKIP:
        args['phonetic_field'] = best_field(DEF_PHONETIC_FIELDS, g.fields)

    # Reading map options
    icon_label       = best_field(DEF_LABEL_FIELDS,  g.fields)
    icon_weight      = best_field(DEF_WEIGHT_FIELDS, g.fields)
    icon_color       = best_field(DEF_COLOR_FIELDS,  g.fields)
    icon_type        = DEF_ICON_TYPE
    link_duplicates  = DEF_LINK_DUPLICATES
    draw_join_fields = DEF_DRAW_JOIN_FIELDS

    if len(args['map_options']) >= 1 and args['map_options'][0] != SKIP:
        icon_label = args['map_options'][0]

    if len(args['map_options']) >= 2 and args['map_options'][1] != SKIP:
        icon_weight = None if args['map_options'][1] == DISABLE else args['map_options'][1]

    if len(args['map_options']) >= 3 and args['map_options'][2] != SKIP:
        icon_color = None if args['map_options'][2] == DISABLE else args['map_options'][2]

    if len(args['map_options']) >= 4 and args['map_options'][3] != SKIP:
        icon_type = None if args['map_options'][3] == DISABLE else args['map_options'][3]

    if len(args['map_options']) >= 5 and args['map_options'][4] != SKIP:
        link_duplicates = args['map_options'][4] in TRUTHY

    if len(args['map_options']) >= 6 and args['map_options'][5] != SKIP:
        draw_join_fields = args['map_options'][5] in TRUTHY

    # Reading graph options
    # Default graph_fields is first two available from DEF_GRAPH_FIELDS
    graph_fields = [f for f in DEF_GRAPH_FIELDS if f in g.fields][0:2]
    graph_weight = DEF_GRAPH_WEIGHT

    if len(args['graph_fields']) >= 1:
        # If user gave something for forget the defaults
        graph_fields = [f for f in args['graph_fields'] if f != SKIP]

    if args['graph_weight'] != SKIP:
        graph_weight = args['graph_weight']

    # Reading quiet options
    quiet_delimiter = DEF_QUIET_DELIM
    header_display  = DEF_QUIET_HEADER

    if len(args['quiet_options']) >= 1 and args['quiet_options'][0] != SKIP:
        quiet_delimiter = args['quiet_options'][0]

    if len(args['quiet_options']) >= 2 and args['quiet_options'][1] != SKIP:
        header_display = args['quiet_options'][1]

    # Reading interactive query options
    interactive_field = best_field(DEF_INTER_FIELDS, g.fields)
    interactive_type  = DEF_INTER_TYPE

    if interactive_query_mode:
        if len(args['interactive_query']) >= 1 and args['interactive_query'][0] != SKIP:
            interactive_field = args['interactive_query'][0]

        if len(args['interactive_query']) >= 2 and args['interactive_query'][1] != SKIP:
            interactive_type = args['interactive_query'][1]

    # Reading phonetic options
    phonetic_method = args['phonetic_method']

    # show / omit
    if args['omit'] == SKIP:
        args['omit'] = DEF_OMIT_FIELDS

    if args['show'] == SKIP:
        args['show'] = DEF_SHOW_FIELDS

    if args['show_additional'] == SKIP:
        args['show_additional'] = DEF_SHOW_ADD_FIELDS



    #
    # FAILING
    #
    # Failing on lack of opentrep support if necessary
    if args['trep'] is not None:
        if not g.hasTrepSupport():
            error('trep_support')

    # Failing on lack of geocode support if necessary
    if args['near'] is not None or args['closest'] is not None:
        if not g.hasGeoSupport():
            error('geocode_support', g.data)

    # Failing on wrong headers
    if args['exact'] is not None:
        for field in exact_fields:
            if field not in g.fields:
                error('field', field, g.data, sorted(g.fields))

    if args['fuzzy'] is not None:
        if args['fuzzy_field'] not in g.fields:
            error('field', args['fuzzy_field'], g.data, sorted(g.fields))

    if args['phonetic'] is not None:
        if args['phonetic_field'] not in g.fields:
            error('field', args['phonetic_field'], g.data, sorted(g.fields))

    # Failing on unknown fields
    fields_to_test = [
        f for f in (icon_label, icon_weight, icon_color, interactive_field, graph_weight)
        if f is not None
    ] + graph_fields

    for field in args['show'] + args['show_additional'] + args['omit'] + fields_to_test:
        field, ext_field = check_ext_field(g, field)

        if field not in [REF] + g.fields:
            # Join fields call are ok, but they are not in self.fields
            if not g.hasJoin(field):
                error('field', field, g.data, sorted([REF]    + \
                                                     g.fields + \
                                                     ['(join) %s:' % SPLIT.join(k) for k in g._join]))

        if ext_field is not None:
            ext_g = g.getJoinBase(field)
            if ext_field not in [REF] + ext_g.fields + ['__loc__']:
                error('field', ext_field, ext_g.data, sorted([REF] + ext_g.fields))

    # Testing icon_type from -M
    if icon_type not in ALLOWED_ICON_TYPES:
        error('wrong_value', icon_type, ALLOWED_ICON_TYPES)

    # Testing -I option
    if interactive_type not in ALLOWED_INTER_TYPES:
        error('wrong_value', interactive_type, ALLOWED_INTER_TYPES)

    # Testing -y option
    if phonetic_method not in ALLOWED_PHONETIC_METHODS:
        error('wrong_value', phonetic_method, ALLOWED_PHONETIC_METHODS)


    #
    # MAIN
    #
    if verbose:
        if not stdin.isatty() and interactive_query_mode:
            print 'Looking for matches from stdin query: %s search %s' % \
                    (interactive_type,
                     '' if interactive_type == '__key__' else 'on %s...' % interactive_field)
        elif args['keys']:
            print 'Looking for matches from %s...' % ', '.join(args['keys'])
        else:
            print 'Looking for matches from *all* data...'

    # Keeping track of last filter applied
    last = None

    # Keeping only keys in intermediate search
    ex_keys = lambda res : None if res is None else (e[1] for e in res)

    # We start from either all keys available or keys listed by user
    # or from stdin if there is input
    if not stdin.isatty() and interactive_query_mode:
        values = [row.strip() for row in stdin]
        # Query type
        if interactive_type == '__key__':
            res = enumerate(values)
            last = None

        elif interactive_type == '__exact__':
            # Indexing if not already done at init
            g.addIndex(interactive_field, verbose=logorrhea)

            res = []
            for val in values:
                conditions = [(interactive_field, val)]
                res.extend(list(g.findWith(conditions, force_str=FORCE_STR, mode='or', verbose=logorrhea)))

            # Other way to do it by putting all lines in one *or* condition
            # But for over 1000 lines, this becomes slower than querying each line
            #conditions = [(interactive_field, val) for val in values]
            #res = g.findWith(conditions, force_str=FORCE_STR, mode='or', verbose=logorrhea)
            last = 'exact'

        elif interactive_type == '__fuzzy__':
            res = []
            for val in values:
                res.extend(list(g.fuzzyFindCached(val, interactive_field, min_match=args['fuzzy_limit'], verbose=logorrhea)))
            last = 'fuzzy'

        elif interactive_type == '__phonetic__':
            res = []
            for val in values:
                res.extend(list(g.phoneticFind(val, interactive_field, method=phonetic_method, verbose=logorrhea)))
            last = 'phonetic'

    elif args['keys']:
        res = enumerate(args['keys'])
    else:
        res = enumerate(g)

    # We are going to chain conditions
    # res will hold intermediate results
    if args['trep'] is not None:
        args['trep'] = ' '.join(args['trep'])
        if verbose:
            print '(*) Applying: trep search on "%s" (output %s)' % (args['trep'], args['trep_format'])

        res = g.trepSearch(args['trep'], trep_format=args['trep_format'], from_keys=ex_keys(res), verbose=verbose)
        last = 'trep'


    if args['exact'] is not None:
        args['exact'] = ' '.join(args['exact'])

        exact_values = args['exact'].split(SPLIT, len(exact_fields) - 1)
        conditions = list(izip_longest(exact_fields, exact_values, fillvalue=''))
        mode = 'or' if args['or'] else 'and'

        if verbose:
            if args['reverse']:
                print '(*) Applying: field %s' % (' %s ' % mode).join('%s != "%s"' % c for c in conditions)
            else:
                print '(*) Applying: field %s' % (' %s ' % mode).join('%s == "%s"' % c for c in conditions)

        res = list(g.findWith(conditions, from_keys=ex_keys(res), reverse=args['reverse'], mode=mode, force_str=FORCE_STR, verbose=logorrhea))
        last = 'exact'


    if args['fuzzy'] is not None:
        args['fuzzy'] = ' '.join(args['fuzzy'])
        if verbose:
            print '(*) Applying: field %s ~= "%s" (%.1f%%)' % (args['fuzzy_field'], args['fuzzy'], 100 * args['fuzzy_limit'])

        res = list(g.fuzzyFind(args['fuzzy'], args['fuzzy_field'], min_match=args['fuzzy_limit'], from_keys=ex_keys(res)))
        last = 'fuzzy'


    if args['phonetic'] is not None:
        args['phonetic'] = ' '.join(args['phonetic'])
        if verbose:
            print '(*) Applying: field %s sounds ~ "%s" with %s' % (args['phonetic_field'], args['phonetic'], phonetic_method)

        res = sorted(g.phoneticFind(args['phonetic'], args['phonetic_field'], method=phonetic_method, from_keys=ex_keys(res), verbose=logorrhea))
        last = 'phonetic'


    if args['near'] is not None:
        args['near'] = ' '.join(args['near'])
        if verbose:
            print '(*) Applying: near %s km from "%s" (%s grid)' % (args['near_limit'], args['near'], 'with' if with_grid else 'without')

        coords = scan_coords(args['near'], g, verbose)
        res = sorted(g.findNearPoint(coords, radius=args['near_limit'], grid=with_grid, from_keys=ex_keys(res)))
        last = 'near'


    if args['closest'] is not None:
        args['closest'] = ' '.join(args['closest'])
        if verbose:
            print '(*) Applying: closest %s from "%s" (%s grid)' % (args['closest_limit'], args['closest'], 'with' if with_grid else 'without')

        coords = scan_coords(args['closest'], g, verbose)
        res = list(g.findClosestFromPoint(coords, N=args['closest_limit'], grid=with_grid, from_keys=ex_keys(res)))
        last = 'closest'



    #
    # DISPLAY
    #

    # Saving to list
    res = list(res)

    # We clock the time here because now the res iterator has been used
    if logorrhea:
        end = datetime.now()
        print 'Done in %s = (load) %s + (search) %s' % \
                (end - before_init, after_init - before_init, end - after_init)

    # Removing unknown keys
    for h, k in res:
        if k not in g:
            warn('key', k, g.data, g.loaded)

    res = [(h, k) for h, k in res if k in g]


    # Keeping only "limit" first results
    nb_res_ini = len(res)

    if limit is not None:
        res = res[:limit]

    nb_res = len(res)

    if verbose:
        print 'Keeping %s result(s) from %s initially...' % (nb_res, nb_res_ini)


    # Highlighting some rows
    important = set(['__key__'])

    if args['exact'] is not None:
        for prop in exact_fields:
            important.add(prop)

    if args['fuzzy'] is not None:
        important.add(args['fuzzy_field'])

    if args['phonetic'] is not None:
        important.add(args['phonetic_field'])

    if interactive_query_mode:
        important.add(interactive_field)

    # reference may be different thing depending on the last filter
    if last in ['near', 'closest']:
        ref_type = 'distance'
    elif last in ['trep', 'fuzzy']:
        ref_type = 'percentage'
    elif last in ['phonetic']:
        ref_type = 'phonemes'
    else:
        ref_type = 'index'

    # Display
    if frontend == 'map':
        visu_info = g.visualize(output=g.data,
                                icon_label=icon_label,
                                icon_weight=icon_weight,
                                icon_color=icon_color,
                                icon_type=icon_type,
                                from_keys=ex_keys(res),
                                add_lines=None,
                                add_anonymous_icons=None,
                                add_anonymous_lines=None,
                                link_duplicates=link_duplicates,
                                draw_join_fields=draw_join_fields,
                                catalog=None,
                                line_colors=None,
                                verbose=True)

        rendered, (templates, _) = visu_info

        if templates and verbose:
            display_browser(templates, nb_res)
            launch_http_server(ADDRESS, PORT)

        if 'map' not in rendered:
            # Happens if you try to use --map
            # on non geographical data
            frontend = 'terminal'
            res = res[:DEF_NUM_COL]

            print '/!\ Map template not rendered. Switching to terminal frontend...'


    if frontend == 'graph':
        visu_info = g.graphVisualize(graph_fields=graph_fields,
                                     graph_weight=graph_weight,
                                     with_types=args['with_types'],
                                     from_keys=ex_keys(res),
                                     output=g.data,
                                     verbose=verbose)

        rendered, (templates, _) = visu_info

        if templates and verbose:
            display_browser(templates, nb_res)
            launch_http_server(ADDRESS, PORT)
        else:
            # In quiet mode we do not launch the server
            # but we display the graph structure
            print json.dumps(g.buildGraphData(graph_fields=graph_fields,
                                              graph_weight=graph_weight,
                                              with_types=args['with_types'],
                                              directed=False,
                                              from_keys=ex_keys(res)),
                             indent=4)


    if frontend == 'terminal':
        shown_fields = fields_to_show(g.fields,
                                      set(args['omit']),
                                      args['show'],
                                      args['show_additional'])

        print
        display(g, res, shown_fields, ref_type, important)

    if frontend == 'quiet':
        defaults = [f for f in g.fields if '%s@raw' % f not in g.fields]

        shown_fields = fields_to_show(defaults,
                                      set(args['omit']),
                                      args['show'],
                                      args['show_additional'])

        display_quiet(g, res, shown_fields, ref_type, quiet_delimiter, header_display)

    if verbose and not IS_WINDOWS:
        for warn_msg in ENV_WARNINGS:
            print dedent(warn_msg),


def _test():
    """When called directly, launching doctests.
    """
    import doctest
    doctest.testmod()


if __name__ == '__main__':
    #_test()
    main()

