/**
 * Convenience wrappers around the ‘got’ HTTP client library.
 *
 * Copyright (C) 2025 Paulo Ferreira de Castro
 * Licensed under the Open Software License version 3.0, a copy of which can be
 * found in the LICENSE file.
 */

import { inspect } from "node:util";

import type {
    AfterResponseHook,
    BeforeRequestHook,
    Hooks,
    Options,
    OptionsOfJSONResponseBody,
    OptionsWithPagination,
    Response,
} from "got";
import { RequestError } from "got";

import { getLogger } from "./util.js";

const log = () => getLogger();

export function isCode404(err: RequestError) {
    return err.code === "ERR_NON_2XX_3XX_RESPONSE" && err.response?.statusCode === 404;
}

export function is404(err: unknown): boolean {
    return !!err && err instanceof RequestError && isCode404(err);
}

export function getCommonGotOpts<ElementType, BodyType>(opts: {
    url: string;
    debug: boolean;
    token?: string;
    username?: string;
    password?: string;
}): OptionsOfJSONResponseBody & OptionsWithPagination<ElementType, BodyType> {
    const { url, debug, token, username, password } = opts;
    const gotOpts: OptionsOfJSONResponseBody &
        OptionsWithPagination<ElementType, BodyType> = {
        url,
        responseType: "json",
    };
    if (token) {
        gotOpts.headers = { Authorization: `Bearer ${token}` };
    } else if (username && password) {
        gotOpts.username = username;
        gotOpts.password = password;
    }
    setDebugHooks({ gotOpts, debug });
    return gotOpts;
}

export function debugResponse(res: Response) {
    log().silly("response headers:\n%s", inspect(res.headers, { depth: 5 }));
    log().silly("response body:\n%s", inspect(res.body, { depth: 5 }));
}

export function getAfterResponseDebugHook(): AfterResponseHook {
    return (res: Response) => {
        debugResponse(res);
        return res;
    };
}

export function getBeforeRequestDebugHook(): BeforeRequestHook {
    return (options: Options) => {
        log().silly(
            "beforeRequest hook for url='%s'\noptions.headers: %s",
            options.url,
            inspect(options.headers, { depth: 5 }),
        );
    };
}

export function getDebugHooks(): Partial<Hooks> {
    return {
        afterResponse: [getAfterResponseDebugHook()],
        beforeRequest: [getBeforeRequestDebugHook()],
    };
}

export function setDebugHooks(opts: {
    gotOpts: { hooks?: Partial<Hooks> };
    debug: boolean;
}) {
    const { gotOpts, debug } = opts;
    if (debug) {
        gotOpts.hooks = getDebugHooks();
    }
}
