/* eslint-disable */
/**
 * Ledger API surface, projected from ./openapi.json (exported from the live
 * FastAPI app via scripts/export-openapi.py).
 *
 * NOTE: this file is checked in rather than emitted by `pnpm gen:api`
 * because openapi-typescript@7.13 cannot run in this workspace yet: it
 * imports the classic `typescript` compiler API (`ts.factory`), which
 * typescript@7 (native) no longer provides, and the pnpm peer override that
 * would pin the generator to typescript@6 does not take effect. When that
 * is resolved, `gen:api` output replaces this file. Until then, after any
 * ledger route change: re-export openapi.json and update this projection
 * to match (parameters below mirror the live spec exactly).
 */
export interface paths {
  "/health": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get: {
      parameters: {
        query?: never;
        header?: never;
        path?: never;
        cookie?: never;
      };
      requestBody?: never;
      responses: {
        /** @description Successful Response */
        200: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": unknown;
          };
        };
        /** @description Validation Error */
        422: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": components["schemas"]["HTTPValidationError"];
          };
        };
      };
    };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/demo/onboard": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get: {
      parameters: {
        query?: never;
        header?: never;
        path?: never;
        cookie?: never;
      };
      requestBody?: never;
      responses: {
        /** @description Successful Response */
        200: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": unknown;
          };
        };
        /** @description Validation Error */
        422: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": components["schemas"]["HTTPValidationError"];
          };
        };
      };
    };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/users": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get: {
      parameters: {
        query?: never;
        header?: never;
        path?: never;
        cookie?: never;
      };
      requestBody?: never;
      responses: {
        /** @description Successful Response */
        200: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": unknown;
          };
        };
        /** @description Validation Error */
        422: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": components["schemas"]["HTTPValidationError"];
          };
        };
      };
    };
    put?: never;
    post: {
      parameters: {
        query?: never;
        header?: never;
        path?: never;
        cookie?: never;
      };
      requestBody: {
        content: {
          "application/json": {
            [key: string]: unknown;
          };
        };
      };
      responses: {
        /** @description Successful Response */
        200: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": unknown;
          };
        };
        /** @description Validation Error */
        422: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": components["schemas"]["HTTPValidationError"];
          };
        };
      };
    };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/users/{username}/disable": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post: {
      parameters: {
        query?: never;
        header?: never;
        path: {
          username: string;
        };
        cookie?: never;
      };
      requestBody?: never;
      responses: {
        /** @description Successful Response */
        200: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": unknown;
          };
        };
        /** @description Validation Error */
        422: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": components["schemas"]["HTTPValidationError"];
          };
        };
      };
    };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/tenants": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post: {
      parameters: {
        query?: never;
        header?: never;
        path?: never;
        cookie?: never;
      };
      requestBody: {
        content: {
          "application/json": {
            [key: string]: unknown;
          };
        };
      };
      responses: {
        /** @description Successful Response */
        200: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": unknown;
          };
        };
        /** @description Validation Error */
        422: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": components["schemas"]["HTTPValidationError"];
          };
        };
      };
    };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/projects": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get: {
      parameters: {
        query?: {
          tenant?: string | null;
        };
        header?: never;
        path?: never;
        cookie?: never;
      };
      requestBody?: never;
      responses: {
        /** @description Successful Response */
        200: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": unknown;
          };
        };
        /** @description Validation Error */
        422: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": components["schemas"]["HTTPValidationError"];
          };
        };
      };
    };
    put?: never;
    post: {
      parameters: {
        query?: never;
        header?: never;
        path?: never;
        cookie?: never;
      };
      requestBody: {
        content: {
          "application/json": {
            [key: string]: unknown;
          };
        };
      };
      responses: {
        /** @description Successful Response */
        200: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": unknown;
          };
        };
        /** @description Validation Error */
        422: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": components["schemas"]["HTTPValidationError"];
          };
        };
      };
    };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/onboard/register": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post: {
      parameters: {
        query?: never;
        header?: never;
        path?: never;
        cookie?: never;
      };
      requestBody: {
        content: {
          "application/json": {
            [key: string]: unknown;
          };
        };
      };
      responses: {
        /** @description Successful Response */
        200: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": unknown;
          };
        };
        /** @description Validation Error */
        422: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": components["schemas"]["HTTPValidationError"];
          };
        };
      };
    };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/onboard/login": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post: {
      parameters: {
        query?: never;
        header?: never;
        path?: never;
        cookie?: never;
      };
      requestBody: {
        content: {
          "application/json": {
            [key: string]: unknown;
          };
        };
      };
      responses: {
        /** @description Successful Response */
        200: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": unknown;
          };
        };
        /** @description Validation Error */
        422: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": components["schemas"]["HTTPValidationError"];
          };
        };
      };
    };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/onboard/complete": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post: {
      parameters: {
        query?: never;
        header?: never;
        path?: never;
        cookie?: never;
      };
      requestBody: {
        content: {
          "application/json": {
            [key: string]: unknown;
          };
        };
      };
      responses: {
        /** @description Successful Response */
        200: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": unknown;
          };
        };
        /** @description Validation Error */
        422: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": components["schemas"]["HTTPValidationError"];
          };
        };
      };
    };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/knowledge": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post: {
      parameters: {
        query?: never;
        header?: never;
        path?: never;
        cookie?: never;
      };
      requestBody: {
        content: {
          "application/json": {
            [key: string]: unknown;
          };
        };
      };
      responses: {
        /** @description Successful Response */
        200: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": unknown;
          };
        };
        /** @description Validation Error */
        422: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": components["schemas"]["HTTPValidationError"];
          };
        };
      };
    };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/knowledge/search": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get: {
      parameters: {
        query: {
          q: string;
          project?: string | null;
          n?: number;
          full?: boolean;
        };
        header?: never;
        path?: never;
        cookie?: never;
      };
      requestBody?: never;
      responses: {
        /** @description Successful Response */
        200: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": unknown;
          };
        };
        /** @description Validation Error */
        422: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": components["schemas"]["HTTPValidationError"];
          };
        };
      };
    };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/knowledge/{key}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get: {
      parameters: {
        query?: {
          project?: string | null;
        };
        header?: never;
        path: {
          key: string;
        };
        cookie?: never;
      };
      requestBody?: never;
      responses: {
        /** @description Successful Response */
        200: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": unknown;
          };
        };
        /** @description Validation Error */
        422: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": components["schemas"]["HTTPValidationError"];
          };
        };
      };
    };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/knowledge/{kid}/history": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get: {
      parameters: {
        query?: never;
        header?: never;
        path: {
          kid: number;
        };
        cookie?: never;
      };
      requestBody?: never;
      responses: {
        /** @description Successful Response */
        200: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": unknown;
          };
        };
        /** @description Validation Error */
        422: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": components["schemas"]["HTTPValidationError"];
          };
        };
      };
    };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/knowledge/{kid}/versions": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post: {
      parameters: {
        query?: never;
        header?: never;
        path: {
          kid: number;
        };
        cookie?: never;
      };
      requestBody: {
        content: {
          "application/json": {
            [key: string]: unknown;
          };
        };
      };
      responses: {
        /** @description Successful Response */
        200: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": unknown;
          };
        };
        /** @description Validation Error */
        422: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": components["schemas"]["HTTPValidationError"];
          };
        };
      };
    };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/knowledge/{kid}/confirm": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post: {
      parameters: {
        query?: never;
        header?: never;
        path: {
          kid: number;
        };
        cookie?: never;
      };
      requestBody?: never;
      responses: {
        /** @description Successful Response */
        200: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": unknown;
          };
        };
        /** @description Validation Error */
        422: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": components["schemas"]["HTTPValidationError"];
          };
        };
      };
    };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/knowledge/store": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post: {
      parameters: {
        query?: never;
        header?: never;
        path?: never;
        cookie?: never;
      };
      requestBody: {
        content: {
          "application/json": {
            [key: string]: unknown;
          };
        };
      };
      responses: {
        /** @description Successful Response */
        200: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": unknown;
          };
        };
        /** @description Validation Error */
        422: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": components["schemas"]["HTTPValidationError"];
          };
        };
      };
    };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/knowledge/compact": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post: {
      parameters: {
        query?: never;
        header?: never;
        path?: never;
        cookie?: never;
      };
      requestBody: {
        content: {
          "application/json": {
            [key: string]: unknown;
          };
        };
      };
      responses: {
        /** @description Successful Response */
        200: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": unknown;
          };
        };
        /** @description Validation Error */
        422: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": components["schemas"]["HTTPValidationError"];
          };
        };
      };
    };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/knowledge/snapshot": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post: {
      parameters: {
        query?: never;
        header?: never;
        path?: never;
        cookie?: never;
      };
      requestBody: {
        content: {
          "application/json": {
            [key: string]: unknown;
          };
        };
      };
      responses: {
        /** @description Successful Response */
        200: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": unknown;
          };
        };
        /** @description Validation Error */
        422: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": components["schemas"]["HTTPValidationError"];
          };
        };
      };
    };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/knowledge/store-and-close": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post: {
      parameters: {
        query?: never;
        header?: never;
        path?: never;
        cookie?: never;
      };
      requestBody: {
        content: {
          "application/json": {
            [key: string]: unknown;
          };
        };
      };
      responses: {
        /** @description Successful Response */
        200: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": unknown;
          };
        };
        /** @description Validation Error */
        422: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": components["schemas"]["HTTPValidationError"];
          };
        };
      };
    };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/knowledge-rules": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get: {
      parameters: {
        query?: {
          project?: string | null;
        };
        header?: never;
        path?: never;
        cookie?: never;
      };
      requestBody?: never;
      responses: {
        /** @description Successful Response */
        200: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": unknown;
          };
        };
        /** @description Validation Error */
        422: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": components["schemas"]["HTTPValidationError"];
          };
        };
      };
    };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/knowledge/curate": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post: {
      parameters: {
        query?: never;
        header?: never;
        path?: never;
        cookie?: never;
      };
      requestBody: {
        content: {
          "application/json": {
            [key: string]: unknown;
          };
        };
      };
      responses: {
        /** @description Successful Response */
        200: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": unknown;
          };
        };
        /** @description Validation Error */
        422: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": components["schemas"]["HTTPValidationError"];
          };
        };
      };
    };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/tasks": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get: {
      parameters: {
        query?: {
          state?: string | null;
          tenant?: string | null;
        };
        header?: never;
        path?: never;
        cookie?: never;
      };
      requestBody?: never;
      responses: {
        /** @description Successful Response */
        200: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": unknown;
          };
        };
        /** @description Validation Error */
        422: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": components["schemas"]["HTTPValidationError"];
          };
        };
      };
    };
    put?: never;
    post: {
      parameters: {
        query?: never;
        header?: never;
        path?: never;
        cookie?: never;
      };
      requestBody: {
        content: {
          "application/json": {
            [key: string]: unknown;
          };
        };
      };
      responses: {
        /** @description Successful Response */
        200: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": unknown;
          };
        };
        /** @description Validation Error */
        422: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": components["schemas"]["HTTPValidationError"];
          };
        };
      };
    };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/tasks_with_contract": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post: {
      parameters: {
        query?: never;
        header?: never;
        path?: never;
        cookie?: never;
      };
      requestBody: {
        content: {
          "application/json": {
            [key: string]: unknown;
          };
        };
      };
      responses: {
        /** @description Successful Response */
        200: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": unknown;
          };
        };
        /** @description Validation Error */
        422: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": components["schemas"]["HTTPValidationError"];
          };
        };
      };
    };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/tasks/{tid}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get: {
      parameters: {
        query?: never;
        header?: never;
        path: {
          tid: number;
        };
        cookie?: never;
      };
      requestBody?: never;
      responses: {
        /** @description Successful Response */
        200: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": unknown;
          };
        };
        /** @description Validation Error */
        422: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": components["schemas"]["HTTPValidationError"];
          };
        };
      };
    };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/tasks/{tid}/contracts": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post: {
      parameters: {
        query?: never;
        header?: never;
        path: {
          tid: number;
        };
        cookie?: never;
      };
      requestBody: {
        content: {
          "application/json": {
            [key: string]: unknown;
          };
        };
      };
      responses: {
        /** @description Successful Response */
        200: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": unknown;
          };
        };
        /** @description Validation Error */
        422: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": components["schemas"]["HTTPValidationError"];
          };
        };
      };
    };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/tasks/{tid}/evidence": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post: {
      parameters: {
        query?: never;
        header?: never;
        path: {
          tid: number;
        };
        cookie?: never;
      };
      requestBody: {
        content: {
          "application/json": {
            [key: string]: unknown;
          };
        };
      };
      responses: {
        /** @description Successful Response */
        200: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": unknown;
          };
        };
        /** @description Validation Error */
        422: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": components["schemas"]["HTTPValidationError"];
          };
        };
      };
    };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/tasks/{tid}/evidence/lookup": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get: {
      parameters: {
        query: {
          kind: string;
          fence: string;
        };
        header?: never;
        path: {
          tid: number;
        };
        cookie?: never;
      };
      requestBody?: never;
      responses: {
        /** @description Successful Response */
        200: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": unknown;
          };
        };
        /** @description Validation Error */
        422: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": components["schemas"]["HTTPValidationError"];
          };
        };
      };
    };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/tasks/{tid}/transition": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post: {
      parameters: {
        query?: never;
        header?: never;
        path: {
          tid: number;
        };
        cookie?: never;
      };
      requestBody: {
        content: {
          "application/json": {
            [key: string]: unknown;
          };
        };
      };
      responses: {
        /** @description Successful Response */
        200: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": unknown;
          };
        };
        /** @description Validation Error */
        422: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": components["schemas"]["HTTPValidationError"];
          };
        };
      };
    };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/tasks/{tid}/binding": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get: {
      parameters: {
        query?: never;
        header?: never;
        path: {
          tid: number;
        };
        cookie?: never;
      };
      requestBody?: never;
      responses: {
        /** @description Successful Response */
        200: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": unknown;
          };
        };
        /** @description Validation Error */
        422: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": components["schemas"]["HTTPValidationError"];
          };
        };
      };
    };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/tasks/{tid}/assignments/claim": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post: {
      parameters: {
        query?: never;
        header?: never;
        path: {
          tid: number;
        };
        cookie?: never;
      };
      requestBody: {
        content: {
          "application/json": {
            [key: string]: unknown;
          };
        };
      };
      responses: {
        /** @description Successful Response */
        200: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": unknown;
          };
        };
        /** @description Validation Error */
        422: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": components["schemas"]["HTTPValidationError"];
          };
        };
      };
    };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/tasks/{tid}/assignments/reclaim": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post: {
      parameters: {
        query?: never;
        header?: never;
        path: {
          tid: number;
        };
        cookie?: never;
      };
      requestBody: {
        content: {
          "application/json": {
            [key: string]: unknown;
          };
        };
      };
      responses: {
        /** @description Successful Response */
        200: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": unknown;
          };
        };
        /** @description Validation Error */
        422: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": components["schemas"]["HTTPValidationError"];
          };
        };
      };
    };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/tasks/{tid}/leases/renew": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post: {
      parameters: {
        query?: never;
        header?: never;
        path: {
          tid: number;
        };
        cookie?: never;
      };
      requestBody: {
        content: {
          "application/json": {
            [key: string]: unknown;
          };
        };
      };
      responses: {
        /** @description Successful Response */
        200: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": unknown;
          };
        };
        /** @description Validation Error */
        422: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": components["schemas"]["HTTPValidationError"];
          };
        };
      };
    };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/tasks/{tid}/attempts": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post: {
      parameters: {
        query?: never;
        header?: never;
        path: {
          tid: number;
        };
        cookie?: never;
      };
      requestBody: {
        content: {
          "application/json": {
            [key: string]: unknown;
          };
        };
      };
      responses: {
        /** @description Successful Response */
        200: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": unknown;
          };
        };
        /** @description Validation Error */
        422: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": components["schemas"]["HTTPValidationError"];
          };
        };
      };
    };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/tasks/{tid}/results": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post: {
      parameters: {
        query?: never;
        header?: never;
        path: {
          tid: number;
        };
        cookie?: never;
      };
      requestBody: {
        content: {
          "application/json": {
            [key: string]: unknown;
          };
        };
      };
      responses: {
        /** @description Successful Response */
        200: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": unknown;
          };
        };
        /** @description Validation Error */
        422: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": components["schemas"]["HTTPValidationError"];
          };
        };
      };
    };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/grants": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post: {
      parameters: {
        query?: never;
        header?: never;
        path?: never;
        cookie?: never;
      };
      requestBody: {
        content: {
          "application/json": {
            [key: string]: unknown;
          };
        };
      };
      responses: {
        /** @description Successful Response */
        200: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": unknown;
          };
        };
        /** @description Validation Error */
        422: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": components["schemas"]["HTTPValidationError"];
          };
        };
      };
    };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/grants/active": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get: {
      parameters: {
        query: {
          scope: string;
          contract_rev?: string | null;
        };
        header?: never;
        path?: never;
        cookie?: never;
      };
      requestBody?: never;
      responses: {
        /** @description Successful Response */
        200: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": unknown;
          };
        };
        /** @description Validation Error */
        422: {
          headers: {
            [name: string]: unknown;
          };
          content: {
            "application/json": components["schemas"]["HTTPValidationError"];
          };
        };
      };
    };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
}
export type $defs = Record<string, never>;
export interface operations {
  [key: string]: never;
}
export interface components {
  schemas: {
    HTTPValidationError: {
      detail?: components["schemas"]["ValidationError"][];
    };
    ValidationError: {
      loc: (string | number)[];
      msg: string;
      type: string;
    };
  };
  responses: never;
  parameters: never;
  requestBodies: never;
  headers: never;
  pathItems: never;
}
export type webhooks = Record<string, never>;
