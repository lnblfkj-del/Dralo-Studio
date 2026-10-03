/** The serving backend selects the entry; standalone builds default to local. */
export function isCloudDeployment(): boolean {
  return document.querySelector('meta[name="drama-deployment"]')?.getAttribute("content") === "cloud";
}

export function loginDestination(destination?: string): string {
  return typeof destination === "string" && destination.startsWith("/") && !destination.startsWith("//")
    && !destination.includes("\\") && [...destination].every(character => character.charCodeAt(0) > 31)
    && !["/", "/login"].includes(destination.split(/[?#]/)[0] ?? "") ? destination : "/projects";
}
