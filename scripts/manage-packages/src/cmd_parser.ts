/**
 * Command line parsing code.
 *
 * Subcommand parsing was inspired by Kevin Gibbon's gist:
 * https://gist.github.com/bakkot/d14826a356fa7ac7e5d9385c2c794432
 *
 * Copyright (C) 2025 Paulo Ferreira de Castro
 * Licensed under the Open Software License version 3.0, a copy of which can be
 * found in the LICENSE file.
 */

import { ParseArgsOptionsConfig, parseArgs } from "node:util";

export class CmdLineParserError extends Error {}

const HELP_CMD_NAME = "help";
const LIST_CMD_NAME = "list";
const REPORT_CMD_NAME = "report";
const DELETE_CMD_NAME = "delete";

export interface CommonOpts {
    command: string; // command name, e.g. "list" or "help"
}
export interface RepoOpts extends CommonOpts {
    repo: string;
}
export type HelpCmdOpts = RepoOpts;
export type ReportCmdOpts = RepoOpts;
export interface ListCmdOpts extends RepoOpts {
    tags: Set<string>;
}
export interface DeleteCmdOpts extends RepoOpts {
    tags: Set<string>;
    deleteOrphans: boolean;
}
export function isHelpOpts(opts?: CommonOpts): opts is HelpCmdOpts {
    return opts?.command === HELP_CMD_NAME;
}
export function isRepoOpts(opts: CommonOpts): opts is RepoOpts {
    return !!(opts as RepoOpts)?.repo;
}
export function isListCmdOpts(opts?: CommonOpts): opts is ListCmdOpts {
    return opts?.command === LIST_CMD_NAME;
}
export function isReportCmdOpts(opts?: CommonOpts): opts is ReportCmdOpts {
    return opts?.command === REPORT_CMD_NAME;
}
export function isDeleteCmdOpts(opts?: CommonOpts): opts is DeleteCmdOpts {
    return opts?.command === DELETE_CMD_NAME;
}

function printUsage() {
    console.log(`
Manage GitHub container registry (ghcr.io) package versions.

Usage:
node manage-packages.mjs --help
node manage-packages.mjs list repo [tag ...]
node manage-packages.mjs delete [--orphans] repo tag [tag ...]
node manage-packages.mjs report repo

Subcommands:
list    List package versions for the given repo.
delete  Delete package versions for the given repo and tag(s).
report  Inspect the registry to report on the number of package versions,
        number of SHA digests, number of image tags, and to identify metadata
        inconsistencies and orphan digests or package versions.

Positionals:
repo   A repository identifier in the format ‘account/package’, e.g. ‘pdcastro/oh_so_smart’.
tag    A Docker image tag, e.g. ‘latest’ or ‘1.0.1-alpine3.21’.

Options:
--orphans  Whether orphan package versions should be detected and deleted.
           An orphan package version is one whose ‘name’ property contains an
           orhpan image SHA digest, which is a digest that is not listed in any
           image index and that is not itself the digest of an image index.
`);
}

export function parseCmdLine(): RepoOpts | undefined {
    /*
     *  To parse subcommands, 3 calls are made to parseArgs():
     *  1. An initial call with ‘strict: false’ just to get parser token data.
     *     https://nodejs.org/docs/latest/api/util.html#parseargs-tokens
     *  2. A second call to parse the main options (common to all subcommands).
     *  3. A final call to parse the subcommand options.
     */
    const mainOptConfig: ParseArgsOptionsConfig = {
        help: { type: "boolean", short: "h" },
    };
    const args = process.argv.slice(2); // Exclude execPath and filename
    const { tokens } = parseArgs({
        options: mainOptConfig,
        args,
        strict: false,
        tokens: true,
    });

    // Find subcommands
    const subcmdIndex = tokens.find((e) => e.kind === "positional")?.index ?? args.length;
    const subcmd = args[subcmdIndex];
    const subcmdArgs = args.slice(subcmdIndex + 1);

    // Parse the main (common) options
    const { values } = parseArgs({
        options: mainOptConfig,
        args: args.slice(0, subcmdIndex),
    });
    if (
        values.help ||
        subcmd === undefined ||
        subcmdArgs.includes("--help") ||
        subcmdArgs.includes("-h")
    ) {
        printUsage();
        return;
    }

    // Parse subcommands
    type parseFuncT = (name: string, args: string[]) => RepoOpts;
    const subcommands: { [key: string]: parseFuncT | undefined } = {
        [LIST_CMD_NAME]: parseListCommand,
        [DELETE_CMD_NAME]: parseDeleteCommand,
        [REPORT_CMD_NAME]: parseReportCommand,
    };
    const parseFunc = subcommands[subcmd];
    if (!parseFunc) {
        throw new CmdLineParserError(
            `Command line parser: Unknown subcommand '${subcmd}'`,
        );
    }
    return parseFunc(subcmd, subcmdArgs);
}

function parseDeleteCommand(name: string, args: string[]): DeleteCmdOpts {
    // delete [--orphans] repo tag [tag ...]
    const options: ParseArgsOptionsConfig = {
        orphans: { type: "boolean" },
    };
    const parsed = parseArgs({ options, args, allowPositionals: true });
    const { values } = parsed;
    let { positionals } = parsed;
    positionals = cleanAndValidatePositionals({ cmdName: name, positionals, minArgs: 2 });
    const repo = validateRepo(positionals[0]);
    const tags = validateTags(positionals.slice(1));
    return { command: name, repo, tags, deleteOrphans: !!values.orphans };
}

function parseListCommand(name: string, args: string[]): ListCmdOpts {
    // list repo [tag ...]
    const options: ParseArgsOptionsConfig = {
        tags: { type: "boolean" },
    };
    let { positionals } = parseArgs({ options, args, allowPositionals: true });
    positionals = cleanAndValidatePositionals({
        cmdName: name,
        positionals,
        minArgs: 1,
    });
    const repo = validateRepo(positionals[0]);
    const tags = validateTags(positionals.slice(1));
    return { command: name, repo, tags };
}

function parseReportCommand(name: string, args: string[]): ReportCmdOpts {
    // report repo
    let { positionals } = parseArgs({ args, allowPositionals: true });
    positionals = cleanAndValidatePositionals({ cmdName: name, positionals, nArgs: 1 });
    const repo = validateRepo(positionals[0]);
    return { command: name, repo };
}

function validateTags(tags: string[]): Set<string> {
    return new Set(tags.map((v) => v.trim()).filter((v) => v));
}

function validateRepo(repo?: string): string {
    if (!repo || repo.indexOf("/") < 1) {
        throw new CmdLineParserError(`Invalid repository argument ‘${repo}’`);
    }
    return repo;
}

function cleanAndValidatePositionals(opts: {
    cmdName: string;
    positionals: string[];
    maxArgs?: number;
    minArgs?: number;
    nArgs?: number;
}): string[] {
    const { cmdName, maxArgs, minArgs, nArgs } = opts;
    let { positionals } = opts;
    if (typeof nArgs === "number" && positionals.length != nArgs) {
        throw new CmdLineParserError(
            `The ‘${cmdName}’ subcommand takes exactly ${nArgs} positional arguments.`,
        );
    }
    if (typeof maxArgs === "number" && positionals.length > maxArgs) {
        throw new CmdLineParserError(
            `The ‘${cmdName}’ subcommand takes at most ${maxArgs} positional arguments.`,
        );
    }
    if (typeof minArgs === "number" && positionals.length < minArgs) {
        throw new CmdLineParserError(
            `The ‘${cmdName}’ subcommand takes at least ${minArgs} positional arguments.`,
        );
    }
    positionals = positionals.map((p) => p.trim());
    if (positionals.some((p) => !p)) {
        throw new CmdLineParserError(
            "Some of the positional command line arguments are blank or empty.",
        );
    }
    return positionals;
}
