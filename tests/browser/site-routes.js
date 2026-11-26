import { readdirSync } from "node:fs";
import { relative, resolve, sep } from "node:path";

const performanceRoutes = ["/", "/moves/", "/lenses/", "/method/"];

function htmlFiles(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const filePath = resolve(directory, entry.name);
    if (entry.isDirectory()) return htmlFiles(filePath);
    return entry.isFile() && /\.html?$/i.test(entry.name) ? [filePath] : [];
  });
}

function routeForFile(siteDirectory, filePath) {
  const relativePath = relative(siteDirectory, filePath).split(sep).join("/");
  if (relativePath === "index.html") return "/";
  if (relativePath.endsWith("/index.html")) {
    return `/${relativePath.slice(0, -"index.html".length)}`;
  }
  return `/${relativePath}`;
}

export function generatedFileForRoute(siteDirectory, route) {
  const filePath = resolve(siteDirectory, `.${route}`);
  return route === "/"
    ? resolve(siteDirectory, "index.html")
    : /\.html?$/i.test(route)
      ? filePath
      : resolve(filePath, "index.html");
}

export function discoverHtmlRoutes(siteDirectory) {
  return htmlFiles(siteDirectory)
    .map((filePath) => routeForFile(siteDirectory, filePath))
    .sort();
}

export function selectSmokeRoutes(siteDirectory) {
  return discoverHtmlRoutes(siteDirectory);
}

export function selectPerformanceRoutes(siteDirectory) {
  const generatedRoutes = new Set(discoverHtmlRoutes(siteDirectory));
  const missingRoutes = performanceRoutes.filter(
    (route) => !generatedRoutes.has(route),
  );
  if (missingRoutes.length) {
    throw new Error(
      `Circle performance routes are missing from generated output: ${missingRoutes.join(", ")}`,
    );
  }
  return [...performanceRoutes];
}
