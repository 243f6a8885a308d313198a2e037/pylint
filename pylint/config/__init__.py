# -*- coding: utf-8 -*-
# Copyright (c) 2006-2010, 2012-2014 LOGILAB S.A. (Paris, FRANCE) <contact@logilab.fr>
# Copyright (c) 2008 pyves@crater.logilab.fr <pyves@crater.logilab.fr>
# Copyright (c) 2010 Julien Jehannet <julien.jehannet@logilab.fr>
# Copyright (c) 2013 Google, Inc.
# Copyright (c) 2013 John McGehee <jmcgehee@altera.com>
# Copyright (c) 2014-2020 Claudiu Popa <pcmanticore@gmail.com>
# Copyright (c) 2014 Brett Cannon <brett@python.org>
# Copyright (c) 2014 Arun Persaud <arun@nubati.net>
# Copyright (c) 2015 Aru Sahni <arusahni@gmail.com>
# Copyright (c) 2015 John Kirkham <jakirkham@gmail.com>
# Copyright (c) 2015 Ionel Cristian Maries <contact@ionelmc.ro>
# Copyright (c) 2016 Erik <erik.eriksson@yahoo.com>
# Copyright (c) 2016 Alexander Todorov <atodorov@otb.bg>
# Copyright (c) 2016 Moises Lopez <moylop260@vauxoo.com>
# Copyright (c) 2017-2019 Ville Skyttä <ville.skytta@iki.fi>
# Copyright (c) 2017 hippo91 <guillaume.peillex@gmail.com>
# Copyright (c) 2017 ahirnish <ahirnish@gmail.com>
# Copyright (c) 2017 Łukasz Rogalski <rogalski.91@gmail.com>
# Copyright (c) 2018, 2020 Anthony Sottile <asottile@umich.edu>
# Copyright (c) 2018 Jim Robertson <jrobertson98atx@gmail.com>
# Copyright (c) 2018 ssolanki <sushobhitsolanki@gmail.com>
# Copyright (c) 2018 Bryce Guinta <bryce.paul.guinta@gmail.com>
# Copyright (c) 2018 Sushobhit <31987769+sushobhit27@users.noreply.github.com>
# Copyright (c) 2018 Gary Tyler McLeod <mail@garytyler.com>
# Copyright (c) 2018 Konstantin <Github@pheanex.de>
# Copyright (c) 2018 Nick Drozd <nicholasdrozd@gmail.com>
# Copyright (c) 2019 Janne Rönkkö <jannero@users.noreply.github.com>
# Copyright (c) 2019 Ashley Whetter <ashley@awhetter.co.uk>
# Copyright (c) 2019 Hugo van Kemenade <hugovk@users.noreply.github.com>
# Copyright (c) 2019 Pierre Sassoulas <pierre.sassoulas@gmail.com>

# Licensed under the GPL: https://www.gnu.org/licenses/old-licenses/gpl-2.0.html
# For details: https://github.com/PyCQA/pylint/blob/master/COPYING

"""utilities for Pylint configuration :

* pylintrc
* pylint.d (PYLINTHOME)
"""
import collections
import configparser
import contextlib
import copy
import functools
import optparse
import os
import pickle
import sys
from typing import Any, Dict, Tuple

import toml

from pylint import utils
from pylint.config.find_default_config_files import find_default_config_files
from pylint.config.man_help_formatter import _ManHelpFormatter
from pylint.config.option import (
    Option,
    _csv_validator,
    _regexp_csv_validator,
    _regexp_validator,
    _validate,
)

__all__ = ["_csv_validator", "_regexp_csv_validator", "_regexp_validator"]

USER_HOME = os.path.expanduser("~")
if "PYLINTHOME" in os.environ:
    PYLINT_HOME = os.environ["PYLINTHOME"]
    if USER_HOME == "~":
        USER_HOME = os.path.dirname(PYLINT_HOME)
elif USER_HOME == "~":
    PYLINT_HOME = ".pylint.d"
else:
    PYLINT_HOME = os.path.join(USER_HOME, ".pylint.d")


def _get_pdata_path(base_name, recurs):
    base_name = base_name.replace(os.sep, "_")
    return os.path.join(PYLINT_HOME, "%s%s%s" % (base_name, recurs, ".stats"))


def load_results(base):
    data_file = _get_pdata_path(base, 1)
    try:
        with open(data_file, "rb") as stream:
            return pickle.load(stream)
    except Exception:  # pylint: disable=broad-except
        return {}


def save_results(results, base):
    if not os.path.exists(PYLINT_HOME):
        try:
            os.mkdir(PYLINT_HOME)
        except OSError:
            print("Unable to create directory %s" % PYLINT_HOME, file=sys.stderr)
    data_file = _get_pdata_path(base, 1)
    try:
        with open(data_file, "wb") as stream:
            pickle.dump(results, stream)
    except OSError as ex:
        print("Unable to create file %s: %s" % (data_file, ex), file=sys.stderr)


def find_pylintrc():
    """search the pylint rc file and return its path if it find it, else None
    """
    for config_file in find_default_config_files():
        if config_file.endswith("pylintrc"):
            return config_file

    return None


PYLINTRC = find_pylintrc()

ENV_HELP = (
    """
The following environment variables are used:
    * PYLINTHOME
    Path to the directory where persistent data for the run will be stored. If
not found, it defaults to ~/.pylint.d/ or .pylint.d (in the current working
directory).
    * PYLINTRC
    Path to the configuration file. See the documentation for the method used
to search for configuration file.
"""
    % globals()  # type: ignore
)


class UnsupportedAction(Exception):
    """raised by set_option when it doesn't know what to do for an action"""


def _level_options(group, outputlevel):
    return [
        option
        for option in group.option_list
        if (getattr(option, "level", 0) or 0) <= outputlevel
        and option.help is not optparse.SUPPRESS_HELP
    ]


def _expand_default(self, option):
    """Patch OptionParser.expand_default with custom behaviour

    This will handle defaults to avoid overriding values in the
    configuration file.
    """
    if self.parser is None or not self.default_tag:
        return option.help
    optname = option._long_opts[0][2:]
    try:
        provider = self.parser.options_manager._all_options[optname]
    except KeyError:
        value = None
    else:
        optdict = provider.get_option_def(optname)
        optname = provider.option_attrname(optname, optdict)
        value = getattr(provider.config, optname, optdict)
        value = utils._format_option_value(optdict, value)
    if value is optparse.NO_DEFAULT or not value:
        value = self.NO_DEFAULT_VALUE
    return option.help.replace(self.default_tag, str(value))


@contextlib.contextmanager
def _patch_optparse():
    orig_default = optparse.HelpFormatter
    try:
        optparse.HelpFormatter.expand_default = _expand_default
        yield
    finally:
        optparse.HelpFormatter.expand_default = orig_default


class OptionParser(optparse.OptionParser):
    def __init__(self, option_class, *args, **kwargs):
        optparse.OptionParser.__init__(self, option_class=Option, *args, **kwargs)

    def format_option_help(self, formatter=None):
        if formatter is None:
            formatter = self.formatter
        outputlevel = getattr(formatter, "output_level", 0)
        formatter.store_option_strings(self)
        result = []
        result.append(formatter.format_heading("Options"))
        formatter.indent()
        if self.option_list:
            result.append(optparse.OptionContainer.format_option_help(self, formatter))
            result.append("\n")
        for group in self.option_groups:
            if group.level <= outputlevel and (
                group.description or _level_options(group, outputlevel)
            ):
                result.append(group.format_help(formatter))
                result.append("\n")
        formatter.dedent()
        # Drop the last "\n", or the header if no options or option groups:
        return "".join(result[:-1])

    def _match_long_opt(self, opt):
        """Disable abbreviations."""
        if opt not in self._long_opt:
            raise optparse.BadOptionError(opt)
        return opt


class OptionsManagerMixIn:
    """Handle configuration from both a configuration file and command line options"""

    def __init__(self, usage, config_file=None, version=None):
        self.config_file = config_file
        self.reset_parsers(usage, version=version)
        # list of registered options providers
        self.options_providers = []
        # dictionary associating option name to checker
        self._all_options = collections.OrderedDict()
        self._short_options = {}
        self._nocallback_options = {}
        self._mygroups = {}
        # verbosity
        self._maxlevel = 0

    def reset_parsers(self, usage="", version=None):
        # configuration file parser
        self.cfgfile_parser = configparser.ConfigParser(
            inline_comment_prefixes=("#", ";")
        )
        # command line parser
        self.cmdline_parser = OptionParser(Option, usage=usage, version=version)
        self.cmdline_parser.options_manager = self
        self._optik_option_attrs = set(self.cmdline_parser.option_class.ATTRS)

    def register_options_provider(self, provider, own_group=True):
        """register an options provider"""
        assert provider.priority <= 0, "provider's priority can't be >= 0"
        for i in range(len(self.options_providers)):
            if provider.priority > self.options_providers[i].priority:
                self.options_providers.insert(i, provider)
                break
        else:
            self.options_providers.append(provider)
        non_group_spec_options = [
            option for option in provider.options if "group" not in option[1]
        ]
        groups = getattr(provider, "option_groups", ())
        if own_group and non_group_spec_options:
            self.add_option_group(
                provider.name.upper(),
                provider.__doc__,
                non_group_spec_options,
                provider,
            )
        else:
            for opt, optdict in non_group_spec_options:
                self.add_optik_option(provider, self.cmdline_parser, opt, optdict)
        for gname, gdoc in groups:
            gname = gname.upper()
            goptions = [
                option
                for option in provider.options
                if option[1].get("group", "").upper() == gname
            ]
            self.add_option_group(gname, gdoc, goptions, provider)

    def add_option_group(self, group_name, _, options, provider):
        # add option group to the command line parser
        if group_name in self._mygroups:
            group = self._mygroups[group_name]
        else:
            group = optparse.OptionGroup(
                self.cmdline_parser, title=group_name.capitalize()
            )
            self.cmdline_parser.add_option_group(group)
            group.level = provider.level
            self._mygroups[group_name] = group
            # add section to the config file
            if (
                group_name != "DEFAULT"
                and group_name not in self.cfgfile_parser._sections
            ):
                self.cfgfile_parser.add_section(group_name)
        # add provider's specific options
        for opt, optdict in options:
            self.add_optik_option(provider, group, opt, optdict)

    def add_optik_option(self, provider, optikcontainer, opt, optdict):
        args, optdict = self.optik_option(provider, opt, optdict)
        option = optikcontainer.add_option(*args, **optdict)
        self._all_options[opt] = provider
        self._maxlevel = max(self._maxlevel, option.level or 0)

    def optik_option(self, provider, opt, optdict):
        """get our personal option definition and return a suitable form for
        use with optik/optparse
        """
        optdict = copy.copy(optdict)
        if "action" in optdict:
            self._nocallback_options[provider] = opt
        else:
            optdict["action"] = "callback"
            optdict["callback"] = self.cb_set_provider_option
        # default is handled here and *must not* be given to optik if you
        # want the whole machinery to work
        if "default" in optdict:
            if (
                "help" in optdict
                and optdict.get("default") is not None
                and optdict["action"] not in ("store_true", "store_false")
            ):
                optdict["help"] += " [current: %default]"
            del optdict["default"]
        args = ["--" + str(opt)]
        if "short" in optdict:
            self._short_options[optdict["short"]] = opt
            args.append("-" + optdict["short"])
            del optdict["short"]
        # cleanup option definition dict before giving it to optik
        for key in list(optdict.keys()):
            if key not in self._optik_option_attrs:
                optdict.pop(key)
        return args, optdict

    def cb_set_provider_option(self, option, opt, value, parser):
        """optik callback for option setting"""
        if opt.startswith("--"):
            # remove -- on long option
            opt = opt[2:]
        else:
            # short option, get its long equivalent
            opt = self._short_options[opt[1:]]
        # trick since we can't set action='store_true' on options
        if value is None:
            value = 1
        self.global_set_option(opt, value)

    def global_set_option(self, opt, value):
        """set option on the correct option provider"""
        self._all_options[opt].set_option(opt, value)

    def generate_config(self, stream=None, skipsections=()):
        """write a configuration file according to the current configuration
        into the given stream or stdout
        """
        options_by_section = {}
        sections = []
        for provider in self.options_providers:
            for section, options in provider.options_by_section():
                if section is None:
                    section = provider.name
                if section in skipsections:
                    continue
                options = [
                    (n, d, v)
                    for (n, d, v) in options
                    if d.get("type") is not None and not d.get("deprecated")
                ]
                if not options:
                    continue
                if section not in sections:
                    sections.append(section)
                alloptions = options_by_section.setdefault(section, [])
                alloptions += options
        stream = stream or sys.stdout
        printed = False
        for section in sections:
            if printed:
                print("\n", file=stream)
            utils.format_section(
                stream, section.upper(), sorted(options_by_section[section])
            )
            printed = True

    def generate_manpage(self, pkginfo, section=1, stream=sys.stdout):
        with _patch_optparse():
            formatter = _ManHelpFormatter()
            formatter.output_level = self._maxlevel
            formatter.parser = self.cmdline_parser
            print(
                formatter.format_head(self.cmdline_parser, pkginfo, section),
                file=stream,
            )
            print(self.cmdline_parser.format_option_help(formatter), file=stream)
            print(formatter.format_tail(pkginfo), file=stream)

    def load_provider_defaults(self):
        """initialize configuration using default values"""
        for provider in self.options_providers:
            provider.load_defaults()

    def read_config_file(self, config_file=None, verbose=None):
        """read the configuration file but do not load it (i.e. dispatching
        values to each options provider)
        """
        helplevel = 1
        while helplevel <= self._maxlevel:
            opt = "-".join(["long"] * helplevel) + "-help"
            if opt in self._all_options:
                break  # already processed

            helpfunc = functools.partial(self.helpfunc, level=helplevel)

            helpmsg = "%s verbose help." % " ".join(["more"] * helplevel)
            optdict = {"action": "callback", "callback": helpfunc, "help": helpmsg}
            provider = self.options_providers[0]
            self.add_optik_option(provider, self.cmdline_parser, opt, optdict)
            provider.options += ((opt, optdict),)
            helplevel += 1
        if config_file is None:
            config_file = self.config_file
        if config_file is not None:
            config_file = os.path.expanduser(config_file)
            if not os.path.exists(config_file):
                raise OSError("The config file {:s} doesn't exist!".format(config_file))

        use_config_file = config_file and os.path.exists(config_file)
        if use_config_file:
            parser = self.cfgfile_parser

            if config_file.endswith(".toml"):
                with open(config_file) as fp:
                    content = toml.load(fp)

                try:
                    sections_values = content["tool"]["pylint"]
                except KeyError:
                    pass
                else:
                    for section, values in sections_values.items():
                        parser._sections[section.upper()] = values
            else:
                # Use this encoding in order to strip the BOM marker, if any.
                with open(config_file, encoding="utf_8_sig") as fp:
                    parser.read_file(fp)

                # normalize sections'title
                for sect, values in list(parser._sections.items()):
                    if sect.startswith("pylint."):
                        sect = sect[len("pylint.") :]
                    if not sect.isupper() and values:
                        parser._sections[sect.upper()] = values

        if not verbose:
            return

        if use_config_file:
            msg = "Using config file {}".format(os.path.abspath(config_file))
        else:
            msg = "No config file found, using default configuration"
        print(msg, file=sys.stderr)

    def load_config_file(self):
        """dispatch values previously read from a configuration file to each
        options provider)
        """
        parser = self.cfgfile_parser
        for section in parser.sections():
            for option, value in parser.items(section):
                try:
                    self.global_set_option(option, value)
                except (KeyError, optparse.OptionError):
                    continue

    def load_configuration(self, **kwargs):
        """override configuration according to given parameters"""
        return self.load_configuration_from_config(kwargs)

    def load_configuration_from_config(self, config):
        for opt, opt_value in config.items():
            opt = opt.replace("_", "-")
            provider = self._all_options[opt]
            provider.set_option(opt, opt_value)

    def load_command_line_configuration(self, args=None):
        """Override configuration according to command line parameters

        return additional arguments
        """
        with _patch_optparse():
            if args is None:
                args = sys.argv[1:]
            else:
                args = list(args)
            (options, args) = self.cmdline_parser.parse_args(args=args)
            for provider in self._nocallback_options:
                config = provider.config
                for attr in config.__dict__.keys():
                    value = getattr(options, attr, None)
                    if value is None:
                        continue
                    setattr(config, attr, value)
            return args

    def add_help_section(self, title, description, level=0):
        """add a dummy option section for help purpose """
        group = optparse.OptionGroup(
            self.cmdline_parser, title=title.capitalize(), description=description
        )
        group.level = level
        self._maxlevel = max(self._maxlevel, level)
        self.cmdline_parser.add_option_group(group)

    def help(self, level=0):
        """return the usage string for available options """
        self.cmdline_parser.formatter.output_level = level
        with _patch_optparse():
            return self.cmdline_parser.format_help()

    def helpfunc(self, option, opt, val, p, level):  # pylint: disable=unused-argument
        print(self.help(level))
        sys.exit(0)


class OptionsProviderMixIn:
    """Mixin to provide options to an OptionsManager"""

    # those attributes should be overridden
    priority = -1
    name = "default"
    options = ()  # type: Tuple[Tuple[str, Dict[str, Any]], ...]
    level = 0

    def __init__(self):
        self.config = optparse.Values()
        self.load_defaults()

    def load_defaults(self):
        """initialize the provider using default values"""
        for opt, optdict in self.options:
            action = optdict.get("action")
            if action != "callback":
                # callback action have no default
                if optdict is None:
                    optdict = self.get_option_def(opt)
                default = optdict.get("default")
                self.set_option(opt, default, action, optdict)

    def option_attrname(self, opt, optdict=None):
        """get the config attribute corresponding to opt"""
        if optdict is None:
            optdict = self.get_option_def(opt)
        return optdict.get("dest", opt.replace("-", "_"))

    def option_value(self, opt):
        """get the current value for the given option"""
        return getattr(self.config, self.option_attrname(opt), None)

    def set_option(self, optname, value, action=None, optdict=None):
        """method called to set an option (registered in the options list)"""
        if optdict is None:
            optdict = self.get_option_def(optname)
        if value is not None:
            value = _validate(value, optdict, optname)
        if action is None:
            action = optdict.get("action", "store")
        if action == "store":
            setattr(self.config, self.option_attrname(optname, optdict), value)
        elif action in ("store_true", "count"):
            setattr(self.config, self.option_attrname(optname, optdict), 0)
        elif action == "store_false":
            setattr(self.config, self.option_attrname(optname, optdict), 1)
        elif action == "append":
            optname = self.option_attrname(optname, optdict)
            _list = getattr(self.config, optname, None)
            if _list is None:
                if isinstance(value, (list, tuple)):
                    _list = value
                elif value is not None:
                    _list = []
                    _list.append(value)
                setattr(self.config, optname, _list)
            elif isinstance(_list, tuple):
                setattr(self.config, optname, _list + (value,))
            else:
                _list.append(value)
        elif action == "callback":
            optdict["callback"](None, optname, value, None)
        else:
            raise UnsupportedAction(action)

    def get_option_def(self, opt):
        """return the dictionary defining an option given its name"""
        assert self.options
        for option in self.options:
            if option[0] == opt:
                return option[1]
        raise optparse.OptionError(
            "no such option %s in section %r" % (opt, self.name), opt
        )

    def options_by_section(self):
        """return an iterator on options grouped by section

        (section, [list of (optname, optdict, optvalue)])
        """
        sections = {}
        for optname, optdict in self.options:
            sections.setdefault(optdict.get("group"), []).append(
                (optname, optdict, self.option_value(optname))
            )
        if None in sections:
            yield None, sections.pop(None)
        for section, options in sorted(sections.items()):
            yield section.upper(), options

    def options_and_values(self, options=None):
        if options is None:
            options = self.options
        for optname, optdict in options:
            yield (optname, optdict, self.option_value(optname))


class ConfigurationMixIn(OptionsManagerMixIn, OptionsProviderMixIn):
    """basic mixin for simple configurations which don't need the
    manager / providers model
    """

    def __init__(self, *args, **kwargs):
        if not args:
            kwargs.setdefault("usage", "")
        OptionsManagerMixIn.__init__(self, *args, **kwargs)
        OptionsProviderMixIn.__init__(self)
        if not getattr(self, "option_groups", None):
            self.option_groups = []
            for _, optdict in self.options:
                try:
                    gdef = (optdict["group"].upper(), "")
                except KeyError:
                    continue
                if gdef not in self.option_groups:
                    self.option_groups.append(gdef)
        self.register_options_provider(self, own_group=False)