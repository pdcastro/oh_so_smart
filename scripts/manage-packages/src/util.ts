/**
 * Miscellaneous uself functions and classes.
 *
 * Copyright (C) 2025 Paulo Ferreira de Castro
 *
 * Licensed under the Open Software License version 3.0, a copy of which can be
 * found in the LICENSE file.
 */

import winston from "winston";
import type { Logger } from "winston";

type LevelT = "error" | "warn" | "info" | "http" | "verbose" | "debug" | "silly";
const LEVELS = ["error", "warn", "info", "http", "verbose", "debug", "silly"];

let log: Logger;

export function getLogger(opts?: { level?: LevelT }) {
    if (!log) {
        const { level = "info" } = opts || {};
        log = setupLogger({ level });
    }
    return log;
}

export function setupLogger(opts: { level: LevelT }): Logger {
    const { level } = opts;
    const { combine, splat, printf, timestamp } = winston.format;
    const stderrLevels = LEVELS;
    return winston.createLogger({
        level,
        format: combine(
            timestamp(),
            splat(),
            printf(
                ({ level, message, timestamp }) =>
                    `${timestamp} ${level.toUpperCase()}: ${message}`,
            ),
        ),
        transports: [new winston.transports.Console({ stderrLevels })],
    });
}

export const controller = new AbortController();

export function boolVar(name: string): boolean {
    const val = process.env[name]?.trim();
    return !!val && !["0", "no", "false", "off"].includes(val.toLowerCase());
}

export class DefaultValueMap<K, V> extends Map<K, V> {
    protected defaultValueCallback: (key: K, map: Map<K, V>) => V;

    constructor(defaultValueCallback: (key: K, map: Map<K, V>) => V) {
        super();
        this.defaultValueCallback = defaultValueCallback;
    }

    /** Return an existing value or a newly assigned default value for ‘key’. */
    get(key: K): V {
        let v = super.get(key);
        if (v === undefined) {
            v = this.defaultValueCallback(key, this);
            this.set(key, v);
        }
        return v;
    }
}

/**
 * Return a Proxy for the ‘target’ object with a getter that sets default values on access.
 *
 * Proxy docs: https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Proxy
 *
 * @param target The target object to be proxied.
 * @param defaultValue The default value to be assigned with Object.assign().
 * @param keyFilter Apply the proxy behavior only to keys that satisfy keyFilter(key).
 * @returns A Proxy for the target object.
 */
export function withDefaultValues<T extends { [key: string | symbol]: object }>(
    target: T,
    defaultValue: T[0],
    keyFilter: (key: keyof T) => boolean,
) {
    return new Proxy<T>(target, {
        /**
         * If property ‘prop’ is not already in the ‘target’ object, assign the default value.
         * @param target The target object.
         * @param prop The property to be set and retrieved.
         * @returns The existing or newly assigned value for target[prop].
         */
        get(target, prop: keyof T, receiver) {
            if (keyFilter(prop)) {
                return (target[prop] ||= Object.assign({}, defaultValue));
            }
            return Reflect.get(target, prop, receiver);
        },
    });
}

/**
 * Iterate sequentially over multiple given iterables.
 *
 * For example, this avois the need of concatenating large arrays,
 * which may be expensive both in memory and time. Sample usage:
 *
 * const a1 = [1, 2, 3];
 * const a2 = [4, 5, 6];
 * const arrayChain = new IterChain(a1, a2);
 * for (const i of arrayChain) {
 *     process.stdout.write(`${i}-`);
 * }
 * Console output:
 * 1-2-3-4-5-6-
 */
export class IterChain<T> {
    private iterables: Iterable<T>[] = [];

    constructor(...iterables: Iterable<T>[]) {
        this.iterables.push(...iterables);
    }

    *[Symbol.iterator]() {
        for (const iterable of this.iterables) {
            for (const i of iterable) {
                yield i;
            }
        }
    }
}
