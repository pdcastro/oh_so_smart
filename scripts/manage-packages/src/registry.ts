/**
 * Query an OCI image registry (such as GHCR.io) using its HTTP API.
 *
 * https://github.com/distribution/distribution/blob/v2.8.3/docs/spec/manifest-v2-2.md
 * https://github.com/distribution/distribution/blob/v3.0.0/docs/content/spec/api.md
 * https://github.com/opencontainers/distribution-spec/blob/main/spec.md#endpoints
 *
 * Copyright (C) 2025 Paulo Ferreira de Castro
 *
 * Licensed under the Open Software License version 3.0, a copy of which can be
 * found in the LICENSE file.
 */

import { inspect } from "node:util";

import got, { RequestError } from "got";
import { OptionsWithPagination, Response } from "got";

import { getCommonGotOpts, isCode404 } from "./got_wrappers.js";
import { getLogger } from "./util.js";

const log = () => getLogger();

class RegistryError extends Error {
    statusCode: number;

    constructor(message: string, statusCode: number = 1) {
        super(message);
        this.statusCode = statusCode;
    }

    static fromRequestError(err: RequestError, altStatusCode: number = 1) {
        return new RegistryError(
            `Request error: code=${err.code} statusCode=${err.response?.statusCode}\n` +
                `${err.message}`,
            err.response?.statusCode || altStatusCode,
        );
    }
}

export interface ImagePlatform {
    architecture: string;
    os: string;
    variant?: string;
}

export interface ImageManifest {
    mediaType: string;
    digest: string;
    size: number;
    platform: ImagePlatform;
    annotations?: { [key: string]: string };
}

export interface ImageIndex {
    schemaVersion: number;
    mediaType: string;
    manifests: ImageManifest[];
}

function isImageIndex(body: unknown): body is ImageIndex {
    if (body && body instanceof Object) {
        const b = body as ImageIndex;
        return !!(
            b.schemaVersion &&
            b.mediaType &&
            b.manifests &&
            Array.isArray(b.manifests)
        );
    }
    return false;
}

interface TagsBody {
    tags: string[];
}

function isTagsBody(body: unknown): body is TagsBody {
    return !!(body && body instanceof Object && Array.isArray((body as TagsBody).tags));
}

interface TokenBody {
    token: string;
}

function isTokenBody(body: unknown): body is TokenBody {
    return !!(body && body instanceof Object && (body as TokenBody).token);
}

/**
 * Get a GHCR.io registry authentication token for subsequent queries to the registry.
 * @param opts.repo E.g. 'pdcastro/oh_so_smart'
 * @param opts.debug If true, print debugging output.
 * @returns A registry authentication token.
 */
export async function getRegistryAuthToken(opts: {
    uriEncodedRepo: string;
    debug?: boolean;
}): Promise<string> {
    const { uriEncodedRepo: repo, debug = false } = opts;
    const url = `https://ghcr.io/token?service=ghcr.io&scope=repository:${repo}:pull`;
    const gotOpts = getCommonGotOpts({
        url,
        debug,
        username: repo.split("/", 1)[0], // 'pdcastro/oh_so_smart' -> 'pdcastro'
        password: process.env.GITHUB_TOKEN,
    });
    try {
        const res = await got<{ token: string }>(gotOpts);
        if (isTokenBody(res.body)) {
            return res.body.token;
        }
    } catch (err: unknown) {
        if (err instanceof RequestError) {
            throw RegistryError.fromRequestError(err);
        }
        throw err;
    }
    throw new RegistryError(`Invalid token response for url='${url}'`);
}

/**
 * Iterate over tags in the registry for the given repository.
 *
 * @param repo E.g. "pdcastro/oh_so_smart"
 * @param debug
 */
export async function forEachTag(
    opts: {
        repo: string;
        token?: string;
        countLimit?: number;
        debug?: boolean;
    },
    cb: (tag: string) => Promise<void>,
) {
    const { countLimit = Infinity, debug = false } = opts;
    const uriEncodedRepo = encodeURI(opts.repo);
    const url = `https://ghcr.io/v2/${uriEncodedRepo}/tags/list`;
    const token = opts.token || (await getRegistryAuthToken({ uriEncodedRepo, debug }));
    type ElementType = string;
    type BodyType = TagsBody;
    const gotOpts = getCommonGotOpts<ElementType, BodyType>({ url, debug, token });
    addTagPagination({ gotOpts, countLimit });

    const tags = await got.paginate.each<ElementType, BodyType>(gotOpts);

    for await (const tag of tags) {
        await cb(tag);
    }
}

/**
 * Get a list of all the tags in the registry for the given repository.
 *
 * @param repo E.g. "pdcastro/oh_so_smart"
 * @param debug
 */
export async function getAllTags(opts: {
    repo: string;
    token?: string;
    countLimit?: number;
    debug?: boolean;
}): Promise<string[]> {
    const { countLimit = Infinity, debug = false } = opts;
    const uriEncodedRepo = encodeURI(opts.repo);
    const url = `https://ghcr.io/v2/${uriEncodedRepo}/tags/list`;
    const token = opts.token || (await getRegistryAuthToken({ uriEncodedRepo, debug }));

    type ElementType = string;
    type BodyType = TagsBody;
    const gotOpts = getCommonGotOpts<ElementType, BodyType>({ url, debug, token });
    addTagPagination({ gotOpts, countLimit });
    try {
        return await got.paginate.all<ElementType, BodyType>(gotOpts);
    } catch (err: unknown) {
        if (err instanceof RequestError) {
            throw RegistryError.fromRequestError(err);
        }
        throw err;
    }
}

function addTagPagination(opts: { gotOpts: OptionsWithPagination; countLimit?: number }) {
    const { gotOpts, countLimit = Infinity } = opts;
    gotOpts.pagination ||= {};
    gotOpts.pagination.transform = (res: Response): string[] => {
        if (isTagsBody(res.body)) {
            return res.body.tags;
        }
        throw new RegistryError(
            `Unexpected response body for url='${gotOpts.url}':\n` +
                `Response body: ${inspect(res.body, { depth: 5 })}`,
        );
    };
    gotOpts.pagination.countLimit = countLimit;
}

/**
 * Fetch a Docker image index (manifest list) for the given repo and tag or SHA digest.
 *
 * Based on specs:
 * https://github.com/distribution/distribution/blob/v2.8.3/docs/spec/manifest-v2-2.md
 * https://github.com/distribution/distribution/blob/v3.0.0/docs/content/spec/api.md
 * https://github.com/opencontainers/distribution-spec/blob/main/spec.md#endpoints
 *
 * Sample output:
 * { schemaVersion: 2,
 *   mediaType: 'application/vnd.oci.image.index.v1+json',
 *   manifests: [
 *     { mediaType: 'application/vnd.oci.image.manifest.v1+json',
 *       digest: 'sha256:c17c0191f2ff5d2bec84db028d6fa58893d02f4591c4ef15a31f6661fc3ee132',
 *       size: 1815,
 *       platform: { architecture: 'amd64', os: 'linux' }
 *     },
 *     { mediaType: 'application/vnd.oci.image.manifest.v1+json',
 *       digest: 'sha256:864c501f7bd214ba9f0c2bfe6eca9aa327384a8eb930258754dd8116749e63a8',
 *       size: 1815,
 *       platform: { architecture: 'arm64', os: 'linux' }
 *     },
 *     { mediaType: 'application/vnd.oci.image.manifest.v1+json',
 *       digest: 'sha256:d2d02730efff2e8438ac07f696bd104ad64ee282dd1555323386302dbccc3755',
 *       size: 1815,
 *       platform: { architecture: 'arm', os: 'linux', variant: 'v7' }
 *     },
 *     { mediaType: 'application/vnd.oci.image.manifest.v1+json',
 *       digest: 'sha256:919b36d4d711f9a078ef242d18b1022f020513e826d2ef597033eb2970969147',
 *       size: 566,
 *       annotations: {
 *          'vnd.docker.reference.digest': 'sha256:c17c0191f2ff5d2bec84db028d6fa58893d02f4591c4ef15a31f6661fc3ee132',
 *          'vnd.docker.reference.type': 'attestation-manifest'
 *       },
 *       platform: { architecture: 'unknown', os: 'unknown' }
 *     },
 *     { mediaType: 'application/vnd.oci.image.manifest.v1+json',
 *       digest: 'sha256:28175f74e12d0076ce382405aa95860c6ee3777aafce5e709783bc247394ddde',
 *       size: 566,
 *       annotations: {
 *          'vnd.docker.reference.digest': 'sha256:864c501f7bd214ba9f0c2bfe6eca9aa327384a8eb930258754dd8116749e63a8',
 *          'vnd.docker.reference.type': 'attestation-manifest'
 *       },
 *       platform: { architecture: 'unknown', os: 'unknown' }
 *     },
 *     { mediaType: 'application/vnd.oci.image.manifest.v1+json',
 *       digest: 'sha256:058e8689e3a78d8867eb29577cd7adf520dac1a99e0edf4c0e38ecd896001864',
 *       size: 566,
 *       annotations: {
 *          'vnd.docker.reference.digest': 'sha256:d2d02730efff2e8438ac07f696bd104ad64ee282dd1555323386302dbccc3755',
 *          'vnd.docker.reference.type': 'attestation-manifest'
 *       },
 *       platform: { architecture: 'unknown', os: 'unknown' }
 *     }
 *   ]
 * }
 *
 * @param opts.repo Repository name, e.g. 'pdcastro/oh_so_smart'
 * @param opts.tag Docker image tag, e.g. '1.0.1-alpine3.21'
 * @param opts.token Registry auth token as produced by getRegistryAuthToken()
 * @param opts.debug Debug flag: true to print debugging output
 */
export async function getImageIndex(opts: {
    repo: string;
    tag: string;
    token?: string;
    debug?: boolean;
}): Promise<ImageIndex | undefined> {
    const { debug = false } = opts;
    const uriEncodedRepo = encodeURI(opts.repo);
    const encodedRef = encodeURIComponent(opts.tag);
    const url = `https://ghcr.io/v2/${uriEncodedRepo}/manifests/${encodedRef}`;
    const token = opts.token || (await getRegistryAuthToken({ uriEncodedRepo, debug }));
    const gotOpts = getCommonGotOpts({ url, debug, token });
    gotOpts.headers ||= {};
    // https://stackoverflow.com/questions/62267417/difference-between-oci-image-manifest-and-docker-v2-2-image-manifest
    // Without the OCI ‘accept’ header below, GHCR.io responds with:
    // errors: [{
    //     code: 'MANIFEST_UNKNOWN',
    //     message: 'OCI index found, but Accept header does not support OCI indexes'
    // }]
    gotOpts.headers.accept = "application/vnd.oci.image.index.v1+json";
    let body: Partial<ImageIndex> = {};
    try {
        body = (await got<Partial<ImageIndex>>(gotOpts)).body;
    } catch (err: unknown) {
        if (err instanceof RequestError) {
            if (isCode404(err)) {
                throw new RegistryError(
                    `Image index not found for tag '${opts.repo}:${opts.tag}'`,
                    404,
                );
            }
            // RequestError may expose sensitive metadata.
            throw RegistryError.fromRequestError(err);
        }
        throw err;
    }
    if (isImageIndex(body)) {
        return body;
    }
    throw new RegistryError(`Invalid image index: url='${url}'\nbody: ${body}`);
}

export async function printImageIndex(
    imageNameTag: string,
    imageIndex?: Partial<ImageIndex>,
    imageIndexOwnDigest?: string,
) {
    const padLen = 19;
    const lines: string[] = ["", `Image index for tag ‘${imageNameTag}’:`];
    if (!imageIndex) {
        lines.push("undefined");
        log().error(lines.join("\n"));
        return;
    }
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
            log().warn(`Missing digest in image manifest`, imageNameTag);
        }
    }
    log().debug(lines.join("\n"));
}
