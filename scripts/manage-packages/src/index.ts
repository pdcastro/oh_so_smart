/**
 * Manage (list and delete) images uploaded to the GitHub Container Registry (ghcr.io).
 *
 * Copyright (C) 2025 Paulo Ferreira de Castro
 *
 * Licensed under the Open Software License version 3.0, a copy of which can be
 * found in the LICENSE file.
 */

import {
    CmdLineParserError,
    RepoOpts,
    parseCmdLine,
    isDeleteCmdOpts,
    isListCmdOpts,
    isReportCmdOpts,
    isRepoOpts,
} from "./cmd_parser.js";
import {
    deletePackageVersions,
    printPackageVersionsForImageTags,
    unpackRepo,
} from "./octo.js";
import { boolVar, controller, getLogger } from "./util.js";

async function runCmd(opts: RepoOpts & { debug?: boolean }) {
    const [account, packageName] = unpackRepo(opts.repo);
    const { debug } = opts;
    if (isListCmdOpts(opts)) {
        await printPackageVersionsForImageTags({
            account,
            packageName,
            tags: opts.tags,
            debug,
        });
    } else if (isDeleteCmdOpts(opts)) {
        await deletePackageVersions({
            account,
            packageName,
            tagsToDelete: opts.tags,
            deleteOrphans: opts.deleteOrphans,
            debug,
        });
    } else if (isReportCmdOpts(opts)) {
        // deletePackageVersions() with the following arguments will not
        // delete any packages, but will produce a report on its findings.
        await deletePackageVersions({
            account,
            packageName,
            tagsToDelete: new Set(),
            deleteOrphans: false,
            debug,
        });
    } else {
        throw new CmdLineParserError("Unrecognised options object");
    }
}

export async function main() {
    const debug = boolVar("DEBUG");
    const log = await getLogger({ level: debug ? "debug" : "info" });
    log.debug("Starting");

    const signal = controller.signal;
    signal.addEventListener("abort", () => {
        let logf = log.info;
        if (signal.reason instanceof Error) {
            process.exitCode ||= 1;
            logf = log.error;
        }
        logf("AbortController signal.reason: %s", signal.reason);
    });

    try {
        let opts: RepoOpts | undefined;
        try {
            opts = parseCmdLine();
        } catch (err) {
            if (err instanceof TypeError) {
                throw new CmdLineParserError(err.message);
            }
            throw err;
        }
        if (!opts || !isRepoOpts(opts)) {
            return;
        }
        await runCmd(opts);
    } catch (err) {
        process.exitCode ||= 1;
        if (err instanceof CmdLineParserError) {
            log.error(err);
            return;
        }
        controller.abort(err);
        // Ensure that the AbortController signal handler gets a chance to run.
        const { setTimeout } = await import("node:timers/promises");
        await setTimeout(50);
    }
}

await main();
