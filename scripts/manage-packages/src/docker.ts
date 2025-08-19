/**
 * Execute ‘docker’ command lines in child processes and handle errors.
 *
 * Copyright (C) 2025 Paulo Ferreira de Castro
 * Licensed under the Open Software License version 3.0, a copy of which can be
 * found in the LICENSE file.
 */

import { promisify } from "node:util";
import { execFile as execFileAsync } from "node:child_process";

import pThrottle from "p-throttle";

import { controller, getLogger } from "./util.js";

const log = () => getLogger();
const execFile = promisify(execFileAsync);

interface ImageManifest {
    digest?: string;
    platform?: { os?: string; architecture?: string; variant?: string };
}

interface ImageIndex {
    manifests?: ImageManifest[];
}

/**
 * Truncate a Docker image SHA digest to 8 characters after the colon.
 *
 * @param s A Docker image SHA digest, e.g. 'sha256:abcd123456789...'.
 * @returns A truncated digest, e.g. 'sha256:abcd1234'.
 */
export function trunc8(s: string): string {
    return s.substring(0, s.indexOf(":") + 9);
}

/**
 * Parse the output of the ‘docker manifest inspect’ command line to extract the
 * list of Docker image SHA digests of the platform-specific Docker images that
 * are part of a multiplatform Docker image index tagged by the given
 * ‘imageNameTag’ parameter. Note that the SHA digest of the multiplatform image
 * index itself is NOT included in the returned array.
 *
 * Authentication with ‘docker login ghcr.io’ (or the ‘docker/login-action’
 * GitHub Action) is expected to have been performed prior to calling this
 * function. For testing on a workstation, a Personal Access Token may be used
 * with a command line such as:
 * $ cat gh_pat | docker login ghcr.io -u pdcastro --password-stdin
 *
 * @param imageNameTag Docker image name and tag, e.g. 'oh_so_smart:1.0.4-alpine3.21'.
 * @returns The image SHA digests that are part of the multiplatform index,
 *     e.g. ['sha256:52107b16...', ...].
 */
export async function getImageDigestsForMultiplatformImage(
    imageNameTag: string,
    indexDigestForLogging?: string,
): Promise<string[]> {
    /**
     * Sample ‘docker manifest inspect’ output:
     *
     * $ docker manifest inspect ghcr.io/pdcastro/oh_so_smart:latest
     * {
     *   "schemaVersion": 2,
     *   "mediaType": "application/vnd.oci.image.index.v1+json",
     *   "manifests": [
     *     {   "mediaType": "application/vnd.oci.image.manifest.v1+json",
     *         "size": 1815,
     *         "digest": "sha256:52107b167d1dd2096beda1555a60a43c3826ffdd9cbf6c504ea09197900ca2b2",
     *         "platform": {
     *             "architecture": "amd64",
     *             "os": "linux"
     *         }
     *     },
     *     {   "mediaType": "application/vnd.oci.image.manifest.v1+json",
     *         "size": 1815,
     *         "digest": "sha256:abba24ba78f43818b6b7cd1f4383711a3f0d5a151afbbfe3651be1a84098269d",
     *         "platform": {
     *             "architecture": "arm64",
     *             "os": "linux"
     *         }
     *     },
     *     {   "mediaType": "application/vnd.oci.image.manifest.v1+json",
     *         "size": 1815,
     *         "digest": "sha256:003d7fc8835c71139809882d2ba04681c7ce14a55ef9544af9dad3c3fbf97173",
     *         "platform": {
     *             "architecture": "arm",
     *             "os": "linux",
     *             "variant": "v7"
     *         }
     *     },
     *     {   "mediaType": "application/vnd.oci.image.manifest.v1+json",
     *         "size": 566,
     *         "digest": "sha256:aa5c97be1f28283f334f6edce4c49f877d3f03a1793ea6e174d73f5a7181f576",
     *         "platform": {
     *             "architecture": "unknown",
     *             "os": "unknown"
     *         }
     *     },
     *     ...
     *   ] }
     */
    const { stdout } = await execFile("docker", [
        "manifest",
        "inspect",
        `ghcr.io/pdcastro/${imageNameTag}`,
    ]);
    const imageIndex: ImageIndex = JSON.parse(stdout);

    printImageIndex(imageIndex, imageNameTag, indexDigestForLogging);

    const digests: string[] = (imageIndex?.manifests || [])
        .map((manifest) => manifest.digest)
        .filter((digest): digest is string => !!digest);

    return digests;
}

export async function printImageIndex(
    imageIndex: ImageIndex,
    imageNameTag: string,
    imageIndexOwnDigest?: string,
) {
    const padLen = 19;
    const lines: string[] = ["", `Image index for tag ‘${imageNameTag}’:`];

    if (imageIndexOwnDigest) {
        const prefix = "Index".padEnd(padLen);
        lines.push(`- ${prefix} ${imageIndexOwnDigest}`);
    }

    for (const manifest of imageIndex?.manifests || []) {
        const [digest, os, arch, variant] = [
            manifest.digest,
            manifest.platform?.os,
            manifest.platform?.architecture,
            manifest.platform?.variant,
        ];
        const archSpec = [arch, variant].filter((v) => v).join("/");
        const prefix = `Img ${os}/${archSpec}`.padEnd(padLen);
        lines.push(`- ${prefix} ${digest}`);
        if (!digest) {
            log().error(
                `‘docker manifest inspect ‘%s’’: missing digest in image manifest`,
                imageNameTag,
            );
        }
    }
    log().info(lines.join("\n"));
}

export async function wrapGetImageDigestsForMultiplatformImage(
    imageNameTag: string,
    indexDigestForLogging?: string,
): Promise<string[]> {
    try {
        return await getImageDigestsForMultiplatformImage(
            imageNameTag,
            indexDigestForLogging,
        );
    } catch (err) {
        const digest = indexDigestForLogging ? ` (${trunc8(indexDigestForLogging)})` : "";
        controller.abort(
            new Error(`Error fetching index for ‘${imageNameTag}’${digest}: ${err}`),
        );
    }
    return [];
}

const throttle = pThrottle({
    limit: 1,
    interval: 100,
    signal: controller.signal,
    strict: true,
});

export const throttledGetImageDigestsForMultiplatformImage = throttle(
    wrapGetImageDigestsForMultiplatformImage,
);
