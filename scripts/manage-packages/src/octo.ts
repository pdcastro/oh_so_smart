/**
 * Manage (list and delete) images uploaded to the GitHub Container Registry (ghcr.io).
 *
 * Copyright (C) 2025 Paulo Ferreira de Castro
 *
 * Licensed under the Open Software License version 3.0, a copy of which can be
 * found in the LICENSE file.
 */

import { inspect } from "node:util";

import type { Endpoints, StrategyInterface } from "@octokit/types";
import type { Logger } from "winston";
import { Octokit } from "octokit";
import pLimit from "p-limit";

import { trunc8 } from "./docker.js";
import { getImageIndex, getRegistryAuthToken, printImageIndex } from "./registry.js";
import { DefaultValueMap, controller, getLogger } from "./util.js";

const log = () => getLogger();

export type PackageVersion =
    Endpoints["GET /user/packages/{package_type}/{package_name}/versions"]["response"]["data"][0];

export type Digest = string;
export type Tag = string;
export type HeadTag = Tag;
export type TagToPkgsMap = Map<HeadTag, PackageVersion[]>;
export type TagGroupMap = Map<Tag, Set<Tag>>;
export const UNKNOWN_TAG = "Unknown";

const HR =
    "--------------------------------------------------------------------------------";

function throwIfAborted(altMsg: string = "Aborted") {
    const signal = controller.signal;
    if (!signal.aborted) {
        return;
    }
    if (signal.reason instanceof Error) {
        throw signal.reason;
    }
    throw new Error(String(signal.reason) || altMsg);
}

async function newOctokit() {
    return new Octokit({ ...(await getAuth()) });
}

async function getAuth(): Promise<{
    authStrategy?: StrategyInterface<[], [], object>;
    auth?: string;
}> {
    if (process.env.GITHUB_ACTIONS) {
        const { createActionAuth } = await import("@octokit/auth-action");
        return { authStrategy: createActionAuth };
    }
    return { auth: process.env.GITHUB_TOKEN };
}

/**
 * Split a GitHub repository string ‘account/repoName’ in its two components.
 * @param repo E.g. 'pdcastro/oh_so_smart'
 * @returns E.g. ['pdcastro', 'oh_so_smart']
 */
export function unpackRepo(repo: string): [string, string] {
    const [account, repoName] = repo.split("/", 2);
    return [account, repoName];
}

export async function showPackageInfo(packageName: string) {
    const octokit = await newOctokit();
    // https://octokit.github.io/rest.js/v22/#packages
    const pkg = await octokit.rest.packages.getPackageForAuthenticatedUser({
        package_type: "container",
        package_name: packageName,
    });
    log().info(inspect(pkg, { depth: 5 }));
}

export async function printAllPackageVersions(opts: { packageName: string }) {
    const { packageName } = opts;
    const octokit = await newOctokit();
    const lines = [HR];
    await forEachPackageVersion({
        packageName,
        octokit,
        cb: async (pkg: PackageVersion) => {
            lines.push(inspect(pkg, { depth: 5 }));
        },
    });
    lines.push(HR);
    console.log(lines.join("\n"));
}

/**
 * Print GHCR package versions for the given tags or for all tags in the registry.
 *
 * The output is grouped by image tag. “Orphan” package versions not referenced
 * by any image index are printed under a tag named “Unknown”.
 *
 * @param opts.account GitHub account, e.g. 'pdcastro'
 * @param opts.packageName GitHub package name, e.g. 'oh_so_smart'
 * @param opts.tags An optional list of image tags whose package versions to print.
 *     If not provided, all package versions are printed (grouped by tag).
 * @param opts.debug Debug flag passed on to lower level libraries.
 */
export async function printPackageVersionsForImageTags(opts: {
    account: string;
    packageName: string;
    tags: Set<string>;
    debug?: boolean;
}) {
    const { packageName, tags } = opts;
    const octokit = await newOctokit();
    const [headTagToPkgVersions, tagGroups] = await getPackageVersionsByImageTag({
        ...opts,
        octokit,
    });
    let pkgCount = 0;
    let tagGroupCount = 0;
    headTagToPkgVersions.forEach((pkgVersions: PackageVersion[], headTag: Tag) => {
        const tagGroup = tagGroups.get(headTag);
        if (!tagGroup) {
            // This should not be possible anyway, but it allays the linters.
            log().error("Missing entry for tag ‘%s’ in TagGroupMap", headTag);
            return;
        }
        if (tags.size && !tags.intersection(tagGroup).size) {
            return;
        }
        const noun = tagGroup.size === 1 ? "tag" : "tags";
        const joinedTags = [...tagGroup.values()].join("’, ‘");
        const lines = [
            HR,
            `‘${packageName}’ package versions for ${noun} ‘${joinedTags}’:`,
        ];
        pkgVersions.forEach((pkg) => lines.push(inspect(pkg, { depth: 5 })));
        lines.push(HR);
        console.log(lines.join("\n"));
        tagGroupCount++;
        pkgCount += pkgVersions.length;
    });
    console.log(
        "Printed %d package versions grouped by %d tag groups",
        pkgCount,
        tagGroupCount,
    );
}

/**
 * Browse the registry to compile a map from image tags to package version objects.
 *
 * Note the mapping between how Docker identifies images and versions and how
 * the GitHub Packages API identifies package versions:
 *
 * - A GitHub “package name” maps to a Docker image name such as ‘oh_so_smart’.
 * - There may be many versions of a Docker image. Docker identifies image
 *   versions by an SHA digest, e.g. ‘sha256:003d7fc8...’, and optionally also
 *   by one or more “tags”, e.g. ‘latest’ and ‘oh_so_smart:1.0.1-alpine3.21’.
 * - The GitHub Packages API identifies package versions by a numeric ID, e.g.
 *   ‘471604751’. A GitHub package version object has a ‘name’ property that
 *   stores the SHA digest of the Docker image stored in that package version.
 *
 * A multiplatform Docker image consists of an “image index” that lists multiple
 * image SHA digests (of platform-specific images and also of “metadata images”
 * such as attestation manifests). The image index itself is an image identified
 * by an SHA digest. The contents of the image index can be retrieved using the
 * OCI registry HTTP API, or using the ‘docker manifest inspect’ command line.
 *
 * In GitHub Packages, each such Docker image (the image index, the platform-
 * specific images, the metadata images) consists of a package version.
 * The package version object for an image index has a populated
 * ‘pkg.metadata.container.tags’ property, e.g.:
 * { ...
 *   metadata: {
 *     package_type: 'container',
 *     container: { tags: [ 'latest', '1.0.1-alpine3.21' ] }
 *   }
 * }
 *
 * The SHA digest of a Docker image (or the SHA digest of a multiplatform image
 * index) may be tagged by multiple “equivalent” tags. A “leader tag” is a tag
 * chosen from a group of equivalent tags to index data structures.
 * This function returns a map indexed by leader tags, and a LeaderTags object
 * that maps leader tags to equivalent tags.
 *
 * @param opts.account GitHub account, e.g. 'pdcastro'
 * @param opts.packageName GitHub package name, e.g. 'oh_so_smart'
 * @param opts.octokit: A suitably authenticated Octokit instance (see newOctokit()).
 * @param opts.debug Debug flag passed on to lower level libraries.
 * @returns A two-tuple of: 1. A Map from head tag to package versions, and
 *     2. A Map from tag to its a set of equivalent tags.
 */
export async function getPackageVersionsByImageTag(opts: {
    account: string;
    packageName: string;
    octokit: Octokit;
    debug?: boolean;
}): Promise<[TagToPkgsMap, TagGroupMap]> {
    const { account, packageName, octokit, debug } = opts;
    const repo = `${account}/${packageName}`;
    const uriEncodedRepo = encodeURI(repo);
    const token = await getRegistryAuthToken({ uriEncodedRepo, debug });
    const pkgVersions: PackageVersion[] = [];
    // tagGroups: Map from tags to the set of all tags that point to the same
    // image index, e.g.:
    // 'latest'           -> new Set(['latest', '1.0.2-alpine3.21'])
    // '1.0.2-alpine3.21' -> new Set(['latest', '1.0.2-alpine3.21'])
    // '1.0.1-alpine3.21' -> new Set(['1.0.1-alpine3.21'])
    // 'Unknown'          -> new Set(['Unknown'])
    const tagGroups: TagGroupMap = new Map<Tag, Set<Tag>>([
        [UNKNOWN_TAG, new Set([UNKNOWN_TAG])],
    ]);
    // Map from digests of an image index (including the image index’s own
    // digest) to the head tag (tags[0]) that points to that image index.
    const digestToHeadTag = new Map<Digest, Tag>();

    const tasks: Promise<void>[] = [];
    const limit = pLimit(5); // Execute only so many tasks simultaneously.
    controller.signal.addEventListener("abort", () => limit.clearQueue());

    const forEachPackageVersionCb = async (pkg: PackageVersion) => {
        pkgVersions.push(pkg);
        const tags = pkg.metadata?.container?.tags || [];
        if (!tags.length) {
            return;
        }
        const tagGroup = new Set(tags);
        tagGroup.forEach((tag) => tagGroups.set(tag, tagGroup));
        tasks.push(
            limit(async () => {
                const headTag = tags[0];
                const digests = await getDigestsFromManifestIndex({
                    repo,
                    tag: headTag,
                    token,
                    abort: true,
                    debug,
                    printImageIndex: false,
                    imageIndexOwnDigestForLogging: pkg.name,
                });
                digests.push(pkg.name); // Image index’s own digest
                digests.forEach((digest) => digestToHeadTag.set(digest, headTag));
            }),
        );
    };
    await forEachPackageVersion({ packageName, octokit, cb: forEachPackageVersionCb });
    // Wait for the async tasks that are querying the registry.
    await Promise.all(tasks);
    // Group package versions by the head tag that points to the image index
    // that points to them.
    const headTagToPkgVersions: TagToPkgsMap = Map.groupBy(
        pkgVersions,
        (pkgV) => digestToHeadTag.get(pkgV.name) || UNKNOWN_TAG,
    );
    return [headTagToPkgVersions, tagGroups];
}

/**
 * Fetch an image index and return a list of its image digests.
 *
 * This function wraps registry.getImageIndex() and handles errors.
 *
 * @param opts.repo Repository name, e.g. 'pdcastro/oh_so_smart'
 * @param opts.tag Docker image tag, e.g. '1.0.1-alpine3.21'
 * @param opts.token Registry auth token as produced by getRegistryAuthToken()
 * @param opts.abort If true, call controller.abort(err) instead of throwing err.
 * @param opts.debug Debug flag: true to print debugging output
 * @param opts.printImageIndex Whether to print the fetched image index.
 * @param opts.imageIndexOwnDigestForLogging The image index’s own SHA digest.
 * @returns A list of the image SHA digests from the image index, e.g.
 *     ['sha256:c17c0191...', 'sha256:864c501f...', ...]
 */
async function getDigestsFromManifestIndex(opts: {
    repo: string;
    tag: string;
    token?: string;
    abort?: boolean;
    debug?: boolean;
    printImageIndex?: boolean;
    imageIndexOwnDigestForLogging?: string;
}): Promise<string[]> {
    const { repo, tag, abort = false } = opts;
    let digests: string[] = [];
    try {
        const manifest = await getImageIndex(opts);
        if (opts.printImageIndex) {
            printImageIndex(
                `${repo}:${tag}`,
                manifest,
                opts.imageIndexOwnDigestForLogging,
            );
        }
        digests = (manifest?.manifests || []).map((manifest) => manifest.digest);
    } catch (err) {
        if (abort) {
            log().error(err);
            controller.abort(err);
            return [];
        }
        throw err;
    }
    if (!digests.length) {
        const err = new Error(`Empty manifest list for tag '${repo}:${tag}'`);
        if (abort) {
            log().error(err);
            controller.abort(err);
            return [];
        }
        throw err;
    }
    return digests;
}

/**
 * Iterate over all package versions of the given ‘packageName’ parameter
 * using pagination, and call callback ‘cb’ for each of them.
 *
 * Sample data for the callback’s ‘pkg’ argument:
 * {
 *   id: 471604758,
 *   name: 'sha256:0f9f97006e9eccbd96f0f0342f79e904849bb06d46729b7cc1f4b837444e8b96',
 *   url: 'https://api.github.com/users/pdcastro/packages/container/oh_so_smart/versions/471604758',
 *   package_html_url: 'https://github.com/users/pdcastro/packages/container/package/oh_so_smart',
 *   created_at: '2025-07-27T19:47:30Z',
 *   updated_at: '2025-07-27T19:47:30Z',
 *   html_url: 'https://github.com/users/pdcastro/packages/container/oh_so_smart/471604758',
 *   metadata: {
 *       package_type: 'container',
 *       container: { tags: [ '1.0.4-alpine3.21', 'latest' ] }
 *   }
 * }
 * Note that ‘metadata.container.tags’ may be an empty array.
 *
 * @param packageName The Docker image not including a tag, e.g. 'oh_so_smart'.
 * @param cb Async callback called and awaited for each package version.
 *     If the callback returns true, the iteration is stopped early.
 */
export async function forEachPackageVersion(opts: {
    packageName: string;
    octokit: Octokit;
    cb: (pkg: PackageVersion, octokit: Octokit) => Promise<boolean | void>;
}) {
    const { packageName, octokit, cb } = opts;
    if (controller.signal.aborted) {
        return;
    }
    const it = octokit.paginate.iterator(
        // https://octokit.github.io/rest.js/v22/#packages
        octokit.rest.packages.getAllPackageVersionsForPackageOwnedByAuthenticatedUser,
        { package_type: "container", package_name: packageName },
    );

    loop: for await (const { data: pkgs } of it) {
        for (const pkg of pkgs) {
            if (controller.signal.aborted || (await cb(pkg, octokit))) {
                break loop;
            }
        }
    }
}

export async function getAllPackageVersions(opts: {
    packageName: string;
    octokit: Octokit;
}): Promise<PackageVersion[]> {
    const { packageName, octokit } = opts;
    return await octokit.paginate(
        octokit.rest.packages.getAllPackageVersionsForPackageOwnedByAuthenticatedUser,
        { package_type: "container", package_name: packageName },
    );
}

/**
 * Delete package versions according to the given parameters.
 *
 * If the ‘tag’ parameter is provided, it should be the Docker image tag of a
 * multiplatform image to be deleted from the registry. This means deleting all
 * of the package versions that correspond to the image SHA digests listed in
 * the multiplatform image index (manifest list), plus the package version that
 * correspons to the SHA digest of the image index itself.
 *
 * If the ‘deleteOrphans’ parameter is true, iterate over all package versions
 * to identify and delete orphan package versions. An orphan package version is
 * one whose name property (e.g. ‘name: 'sha256:0f9f9700...'’) is an orphan
 * image SHA digest, which is a digest not listed in any multiplatform image
 * index and which is not itself the digest of a multiplatform image index.
 *
 * The ‘imageTag’ and ‘deleteOrphans’ parameters can be used at the same time,
 * which is efficient as both operations can be performed during the same
 * iteration over the registry’s package versions.
 *
 * @param account GitHub account name, e.g. 'pdcastro'.
 * @param packageName Docker image name not including a tag, e.g. 'oh_so_smart'.
 * @param tag An optional Docker image tag, e.g. '1.0.4-alpine3.21'.
 * @param deleteOrphans If true, delete orphan package versions (see docs above).
 * @param debug Enable debug logging.
 */
export async function deletePackageVersions(opts: {
    account: string;
    packageName: string;
    tagsToDelete: Set<string>;
    deleteOrphans: boolean;
    debug?: boolean;
}) {
    // Note: we want this function to run even if (!tag && !deleteOrphans)
    // because of the reporting side effect.
    const { packageName, tagsToDelete, deleteOrphans, debug } = opts;
    const logger = log();
    const octokit = await newOctokit();
    const digestToPkgMap = await findOrphanOrTaggedPackageVersions({ ...opts, octokit });

    logRegistryStats({ tagsToDelete, digestToPkgMap, logger, debug });

    let deletedCount = 0;
    for (const [
        digest,
        { id, isOrphan, belongsToTagsToDelete, indexDigest, tags },
    ] of digestToPkgMap) {
        const shortDg = trunc8(digest);
        if (isOrphan) {
            const action = deleteOrphans
                ? "Will delete."
                : "Will not delete (not requested).";
            logger.warn(
                "Found orphan package version of id='%s' digest='%s'. %s",
                id,
                shortDg,
                action,
            );
            if (indexDigest || tags.length) {
                let msg =
                    "Inconsistent application state: Package version of " +
                    `id='${id}' digest='${shortDg}' is marked 'orphan', but: `;
                if (indexDigest) {
                    msg += `it has a non-empty indexDigest attribute '${indexDigest}'; `;
                }
                if (tags.length) {
                    msg += `it has a non-empty tags attribute '${tags.join(", ")}'.`;
                }
                const err = new Error(msg);
                logger.error(err);
                throw err;
            }
        }
        if (id === undefined) {
            logger.warn(
                "Registry inconsistency: Missing image (package version) of digest '%s' " +
                    "listed in the image index of digest='%s' (tags: %s).",
                shortDg,
                trunc8(indexDigest),
                (digestToPkgMap.get(indexDigest)?.tags || []).join(", "),
            );
            continue;
        }
        // ‘belongsToTagToBeDeleted’ indicates that the package version’s SHA
        // digest is part of the image index tagged by the ‘tag’ parameter.
        if (belongsToTagsToDelete || (deleteOrphans && isOrphan)) {
            const tagsReason = `tagged by one of ${Array.from(tagsToDelete)}`;
            logger.info(
                "Deleting package version of id='%s' digest='%s' for reason: %s",
                id,
                shortDg,
                belongsToTagsToDelete ? tagsReason : "orphan",
            );
            await deletePackageVersion({ packageName, packageVersionID: id, octokit });
            deletedCount++;
        }
    }
    logger.info("%d package versions deleted.", deletedCount);
}

async function deletePackageVersion(opts: {
    packageName: string;
    packageVersionID: number;
    octokit: Octokit;
}) {
    throwIfAborted("Error during async task processing");
    const { packageName, packageVersionID, octokit } = opts;
    // https://octokit.github.io/rest.js/v22/#packages
    await octokit.rest.packages.deletePackageVersionForAuthenticatedUser({
        package_type: "container",
        package_name: packageName,
        package_version_id: packageVersionID,
    });
}

interface PkgInfo {
    id?: number;
    isOrphan: boolean;
    belongsToTagsToDelete: boolean;
    // indexDigest refers to the digest of the image index that points to this pkg version.
    indexDigest: string;
    tags: string[]; // Only populated if this package version is an image index.
}

/**
 * Iterate over GHCR.io package versions to delete the ones matching the criteria.
 *
 * See also documentation for function deletePackageVersions().
 *
 * For each package version whose metadata is populated with Docker image tags,
 * use the OCI registry HTTP API to fetch the contents of the respective image
 * index.
 *
 * Implementation note: Instead of iterating over all of the package versions,
 * would it have been more efficient to fetch a list of all of the registry tags
 * using the registry API, and then for each tag fetch a list of its image
 * digests? Probably not, for two reasons:
 * 1. It appears that such approach would not fetch the digests of the image
 *    indexes themselves, which are stored in separate package versions that
 *    also need to be deleted (thus requiring iterating over the package
 *    versions to find the ones that match a certain tag).
 * 2. Deleting a package version requires knowing its GitHub-specific numeric
 *    ID, which again would require iterating over the package versions to find
 *    the matching digest.
 *
 * @param account GitHub account name, e.g. 'pdcastro'.
 * @param packageName Docker image name not including a tag, e.g. 'oh_so_smart'.
 * @param tag An optional Docker image tag, e.g. '1.0.4-alpine3.21'.
 * @param deleteOrphans If true, delete orphan package versions (see docs above).
 * @param debug Enable debug logging.
 * @returns A map from image index digests to PkgInfo.
 */
async function findOrphanOrTaggedPackageVersions(opts: {
    account: string;
    packageName: string;
    octokit: Octokit;
    tagsToDelete: Set<string>;
    debug?: boolean;
}): Promise<Map<Digest, PkgInfo>> {
    const { account, packageName, octokit, tagsToDelete, debug = false } = opts;
    const repo = `${account}/${packageName}`;
    const uriEncodedRepo = encodeURI(repo);
    const token = await getRegistryAuthToken({ uriEncodedRepo, debug });
    const digestToPkgMap = new DefaultValueMap<Digest, PkgInfo>(() => ({
        isOrphan: true,
        belongsToTagsToDelete: false,
        indexDigest: "",
        tags: [],
    }));
    const tasks: Promise<void>[] = [];
    const limit = pLimit(5); // Execute only so many tasks simultaneously.
    controller.signal.addEventListener("abort", () => limit.clearQueue());

    const forEachPackageVersionCb = async (pkg: PackageVersion) => {
        const pkgDigest = pkg.name;
        if (!pkgDigest) {
            const err = new Error(
                `Error: Unexpected falsy ‘name’ (digest) property for package version id=${pkg.id}`,
            );
            controller.abort(err);
            throw err;
        }
        const pkgInfo = digestToPkgMap.get(pkgDigest);
        pkgInfo.id = pkg.id;
        const tags = (pkg.metadata?.container?.tags || []).filter((tag) => tag);
        if (!tags.length) {
            return; // pkg is not a multiplatform image index.
        }
        // Here pkg is a multiplatform image index.
        pkgInfo.indexDigest = pkgDigest;
        pkgInfo.tags = tags;
        pkgInfo.isOrphan = false;
        const belongsToTagsToBeDeleted = !!tagsToDelete.intersection(new Set(tags)).size;
        pkgInfo.belongsToTagsToDelete = belongsToTagsToBeDeleted;
        tasks.push(
            limit(async () => {
                const digests = await getDigestsFromManifestIndex({
                    repo,
                    tag: tags[0],
                    token,
                    debug,
                    abort: true,
                    printImageIndex: false,
                    imageIndexOwnDigestForLogging: pkgDigest,
                });
                digests.forEach((digest) => {
                    const _pkgInfo = digestToPkgMap.get(digest);
                    _pkgInfo.belongsToTagsToDelete = belongsToTagsToBeDeleted;
                    _pkgInfo.isOrphan = false;
                    _pkgInfo.indexDigest = pkgDigest;
                });
            }),
        );
    };
    await forEachPackageVersion({ packageName, octokit, cb: forEachPackageVersionCb });
    // Wait for the async tasks that are querying the registry.
    await Promise.all(tasks);
    throwIfAborted("Error during async task processing");

    return digestToPkgMap;
}

function logRegistryStats(opts: {
    tagsToDelete: Set<string>;
    digestToPkgMap: Map<string, PkgInfo>;
    logger: Logger;
    debug?: boolean;
}) {
    const { tagsToDelete, digestToPkgMap, logger, debug } = opts;
    if (debug) {
        logger.debug("%s", inspect(digestToPkgMap, { depth: 5 }));
    }
    let idCount = 0;
    let orphanCount = 0;
    let overallTagCount = 0;
    let tagToBeDeletedDigestCount = 0;
    digestToPkgMap.forEach((pkg) => {
        idCount += Number(pkg.id !== undefined);
        orphanCount += Number(pkg.isOrphan);
        overallTagCount += pkg.tags.length;
        tagToBeDeletedDigestCount += Number(pkg.belongsToTagsToDelete);
    });
    logger.info("Registry: Found %d GitHub package versions.", idCount);
    logger.info(
        "Registry: Found %d distinct digests (including those listed in image indexes).",
        digestToPkgMap.size,
    );
    logger.info(
        "Registry: Found %d tags in total (not necessarily distinct).",
        overallTagCount,
    );
    if (!tagsToDelete.size && tagToBeDeletedDigestCount) {
        const err = new Error(
            "Inconsistent application state: No tag given for deletion, but found " +
                `'${tagToBeDeletedDigestCount}' digests belonging to such tag.`,
        );
        logger.error(err);
        throw err;
    }
    if (tagsToDelete.size || tagToBeDeletedDigestCount) {
        logger.info(
            "Registry: Found %d digests belonging to tag(s) %s to be deleted.",
            tagToBeDeletedDigestCount,
            Array.from(tagsToDelete),
        );
    }
    (orphanCount ? logger.warn : logger.info)(
        "Registry: Found %d orphan digests.",
        orphanCount,
    );
}
