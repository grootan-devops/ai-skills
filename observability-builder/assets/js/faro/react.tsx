// React wiring: the router integration (views and route-change events named by route template)
// and the error boundary. Pass reactIntegration() to startRum({ instrumentations: [...] }).
import { createReactRouterV7DataOptions, faro, FaroErrorBoundary, ReactIntegration, withFaroRouterInstrumentation } from '@grafana/faro-react';
import { type ReactElement, type ReactNode, useEffect } from 'react';
import { type createBrowserRouter, isRouteErrorResponse, matchRoutes, useRouteError } from 'react-router';

type DataRouter = ReturnType<typeof createBrowserRouter>;
let view = 'default';

// The route template of the deepest match: /orders/:id, never /orders/77.
function template(state: DataRouter['state']): string {
  const parts = state.matches.map((m) => m.route.path ?? '').filter(Boolean);
  return parts.length ? ('/' + parts.join('/')).replace(/\/{2,}/g, '/') : view;
}

// The current view name; pass it to startRum({ view: viewName() }) for the first page.
export const viewName = (): string => view;

// React Router 7 or 8 data router (createBrowserRouter). Faro names views by the matched route
// pattern (/orders/:id), never by the raw path.
export const reactIntegration = (): ReactIntegration =>
  new ReactIntegration({ router: createReactRouterV7DataOptions({ matchRoutes }) });

// Wrap the router once, where it is created: const router = instrumentRouter(createBrowserRouter(routes)).
// Faro's router integration sends route_change events; the view (view_name on every record and a
// view_changed event) is set here from the route template.
export function instrumentRouter(router: DataRouter): DataRouter {
  const wrapped = withFaroRouterInstrumentation(router);
  const update = (state: DataRouter['state']) => {
    view = template(state);
    faro.api?.setView({ name: view });
  };
  update(wrapped.state);
  wrapped.subscribe(update);
  return wrapped;
}

// Reports render errors once (the boundary pushes them); the fallback never shows error details.
export function RumErrorBoundary({ children, fallback }: { children: ReactNode; fallback: ReactElement }) {
  return <FaroErrorBoundary fallback={fallback}>{children}</FaroErrorBoundary>;
}

// A data router catches render, loader and action errors in the route's errorElement before any
// outer boundary sees them. Set errorElement: <RouteErrorReporter fallback={…} /> on the root
// route so each one is reported once. Error responses (404s thrown by loaders) aren't exceptions.
export function RouteErrorReporter({ fallback }: { fallback: ReactElement }) {
  const error = useRouteError();
  useEffect(() => {
    if (error instanceof Error) faro.api?.pushError(error);
    else if (!isRouteErrorResponse(error)) faro.api?.pushError(new Error('route error (non-Error value)'));
  }, [error]);
  return fallback;
}
