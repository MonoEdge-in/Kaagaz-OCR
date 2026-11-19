// What the access screens say (PRD 7 PR 11, #604, section 4 of the plan), as constants and pure
// functions, tested in node (tests/access-words.test.ts). Every name here is React text.

import { ApiError } from "@/lib/api";
import { errorWords } from "@/lib/errorWords";
import type { MovePreview, Person } from "@/lib/types";

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** "Priya", "Priya and Amit", "Priya, Amit and Ravi", "Priya, Amit and 12 more". */
export function namesPreview(people: readonly Person[]): string {
  const names = people.map((person) => person.name);
  if (names.length <= 2) return names.join(" and ");
  if (names.length === 3) return `${names[0]}, ${names[1]} and ${names[2]}`;
  return `${names[0]}, ${names[1]} and ${names.length - 2} more`;
}

/** "1 person", "12 people". */
export const peopleCount = (n: number) => plural(n, "person", "people");

// --- the password step ---------------------------------------------------------------------------

export const PASSWORD_TITLE = "Confirm it's you";
export const PASSWORD_LABEL = "Your Kaagaz password";
export const PASSWORD_BUTTON = "Give myself access";
export const WRONG_PASSWORD = "That password isn't right. Try again.";

/** `what`: "Finance", "“Salaries”", "“Sale deed.pdf”" or "every file (Management)". */
export const selfGrantText = (what: string) =>
  `You're giving yourself access to ${what}. Enter your Kaagaz password to go on. Management and the other admins will get an email saying you gave yourself access. The email doesn't name any file.`;
export const quoted = (name: string) => `“${name}”`;
export const MANAGEMENT_WHAT = "every file (Management)";

// --- toasts --------------------------------------------------------------------------------------

export const ACCESS_REMOVED = "Access removed.";
export const managementToast = (name: string, on: boolean) => `${name}: ${on ? "now" : "no longer"} Management.`;
export const canNowSeeToast = (name: string, place: string) => `${name} can now see ${place}.`;
export const headToast = (name: string, department: string, on: boolean) =>
  on ? `${name} is now head of ${department}.` : `${name} is no longer head of ${department}.`;
export const departmentOnlyToast = (department: string, on: boolean) =>
  on ? `${department} is now for its people only.` : `${department} is open to everyone again.`;
export const newDepartmentToast = (department: string, head: string | null) =>
  head ? `${department} added. ${head} heads it.` : `${department} added. No head yet.`;
export const addedPeopleToast = (n: number, department: string) => `Added ${peopleCount(n)} to ${department}.`;

/** Files given: "Shared with Priya.", "Shared with Priya and Amit.", "Shared with 3 people.",
 * "8 files shared with Priya." */
export function sharedToast(files: number, people: readonly Person[]): string {
  const whom = people.length <= 2 ? namesPreview(people) : peopleCount(people.length);
  return files === 1 ? `Shared with ${whom}.` : `${files} files shared with ${whom}.`;
}

// --- Management ----------------------------------------------------------------------------------

export const makeManagementQuestion = (name: string) =>
  `Make ${name} Management? ${name} will see every file in the organisation, including private folders. Management and the other admins get an email about it.`;
export const stopManagementQuestion = (name: string) =>
  `Stop Management for ${name}? ${name} will see only their departments' files, private folders they are named on and files given to them.`;
export const MANAGEMENT_CHIP = "Management";
export const MANAGEMENT_TITLE = "Sees all files";
export const MANAGEMENT_MEANS = ": sees all files"; // read after the chip, for screen readers
export const NO_DEPARTMENT = "No department";

/** The Users tab's Access cell: "Finance (head), Sales", or "No department". */
export function departmentsText(departments: readonly { name: string; head: boolean }[]): string {
  if (departments.length === 0) return NO_DEPARTMENT;
  return departments.map((each) => (each.head ? `${each.name} (head)` : each.name)).join(", ");
}

// --- remove dialogs ------------------------------------------------------------------------------

export const removeDepartmentQuestion = (name: string, department: string) =>
  `Remove ${name} from ${department}? Files given to ${name} directly stay given.`;
export const removeFolderQuestion = (name: string, folder: string) =>
  `Take ${name} off ${quoted(folder)}? ${name} won't see this folder any more, unless they are Management or head of its department.`;
export const removeFileQuestion = (name: string, file: string) =>
  `Remove ${name}'s access to ${quoted(file)}? This takes away every version of the file.`;
/** Lead decision (#604 review): taking the Head tick away asks first. */
export const removeHeadQuestion = (name: string, department: string) =>
  `Remove ${name} as head of ${department}? ${name} won't see ${department}'s private folders any more, unless they are named on them.`;
export const REMOVE_AS_HEAD = "Remove as head";

// --- the Access page -----------------------------------------------------------------------------

export const ACCESS_PAGE = {
  back: "Back to Users",
  loadFailed: "Could not load this person's access.",
  notFound: "We couldn't find this person.",
  departments: "Departments",
  privateFolders: "Private folders",
  give: "Give access",
  selfHint: "Giving yourself access asks for your password. Management and the other admins get an email.",
  noPrivateFolders: "There are no private folders yet. Make a folder private from Who can see this in Documents.",
  fileSearch: "Find a file",
  noFiles: "No files match.",
  noDepartments: "There is no other department to add.",
} as const;
export const accessTitle = (name: string) => `Access for ${name}`;
export const filesHeading = (name: string) => `Files given to ${name}`;
// Lead decision (#604 review): the app's own word for a turned-off account, "disabled".
export const DISABLED_BANNER = "This account is disabled. Enable it from the Users tab to give access again.";
export const managementLine = (on: boolean) => `Sees all files (Management): ${on ? "Yes" : "No"}`;
export const noDepartmentsText = (name: string) => `${name} isn't in any department yet.`;
export const noFoldersText = (name: string) => `${name} isn't named on any private folder.`;
export const noFilesText = (name: string) => `No files have been given to ${name}.`;
/** "Added 3 Nov 2026 by Ravi", "Given 3 Nov 2026" (by someone no longer listed). */
export const byLine = (verb: string, day: string, by: string | null | undefined) => (by ? `${verb} ${day} by ${by}` : `${verb} ${day}`);

// --- the Access list (#706; /access) -------------------------------------------------------------

export const ACCESS_LIST = {
  title: "Access",
  intro: "Who can see what. Open a person to change their departments, private folders and files given.",
  departments: "Departments",
  people: "People",
  privateFolders: "Private folders",
  noPrivateFolders: "No private folders yet. Open a folder in Documents and choose Make private.",
  loading: "Loading the people…",
  loadFailed: "Could not load the people and private folders.",
  empty: "No one is here yet.",
  addPeople: "Add people on the Users tab",
  open: "Access",
  disabled: "Disabled", // the Users tab's word for a turned-off account
} as const;

// --- the Departments tab -------------------------------------------------------------------------

export const headLine = (heads: readonly Person[]) => (heads.length ? `Head: ${heads.map((h) => h.name).join(", ")}` : "No head yet");
export const DEPARTMENT_ONLY = "Department only";
export const OPEN_TO_EVERYONE = "Open to everyone";
export const KEEP_TO_PEOPLE = "Keep to its people";
export const ADD_PEOPLE = "Add people";
export const noMembersWarning = (department: string) =>
  `No one is in ${department} yet. Only Management and people given a file directly will see its files.`;
export const loadingMembers = (department: string) => `Loading the people in ${department}…`;
export const noMembers = (department: string) => `No one is in ${department} yet.`;
export const NO_ONE_MATCHES = "No one matches.";
export const chosenCount = (n: number) => `${n} chosen`;
export const departmentAdded = (department: string) => `${department} added.`;
export const accessLoadFailed = (why: string) => `Who can see each department did not load. ${why}`;

/** One toast for adding people: who was added, then each person who wasn't and why; null when
 * nothing changed and nothing failed (they were all in it already). */
export function addedPeopleText(added: number, department: string, failed: readonly { person: Person; reason: string }[]): string | null {
  const parts = [
    ...(added > 0 ? [addedPeopleToast(added, department)] : []),
    ...failed.map(({ person, reason }) => `Couldn't add ${person.name}: ${reason.replace(/\.$/, "")}.`),
  ];
  return parts.length ? parts.join(" ") : null;
}

export const headQuestion = (department: string) => `Who heads ${department}?`;
export const headExplained = (department: string) =>
  `The head sees every ${department} file, including its private folders, and chooses who is named on them.`;
export const HEAD_STEP = { choose: "Choose a person", me: "Me", later: "Decide later", make: "Make head" } as const;

export const PREVIEW_LOADING = "Checking who would lose access…";
export const PREVIEW_FAILED = "Couldn't check who would lose access.";
export const keepQuestion = (department: string) => `Keep ${department} to its people?`;
export const openQuestion = (department: string) => `Open ${department} to everyone?`;

/** Department only, on: who loses Finance's files, from the preview. */
export function departmentOnlyText(department: string, losing: readonly Person[], members: number): string {
  const nobody = `No one is in ${department} yet, so only Management will see its files.`;
  if (losing.length === 0) {
    // With nobody in it, "everyone who sees its files is in it" would be wrong.
    return members === 0
      ? `Nobody loses access. ${nobody}`
      : `Nobody loses access: everyone who sees ${department} files today is in ${department} or is Management.`;
  }
  const lose = `${peopleCount(losing.length)} will no longer see ${department} files: ${namesPreview(losing)}. Management and anyone you add to ${department} still will.`;
  return members === 0 ? `${lose} ${nobody}` : lose;
}
export const openText = (department: string, files: number) =>
  `Everyone in the organisation will be able to see ${department}'s ${plural(files, "file", "files")}.`;
export const showAll = (n: number) => `Show all ${n}`;

// --- the one-time card (Home) --------------------------------------------------------------------

export const CARD = {
  title: "Everyone can see every file today.",
  text: "Want to keep some files to some people?",
  steps: ["Name a head for each department.", "Keep a department to its people.", "Make private folders."],
  // #701: heads named alone keep no file to anyone; said in place of the text and steps.
  headsOnly:
    "Heads are named, but every file is still open to everyone. Make a department Department only, or a folder private, to keep files to some people.",
  setUp: "Set up access",
  notNow: "Not now",
} as const;

// --- the library (PR 11b): places, Shared with me, not found -------------------------------------

/** The people icon's words: a department kept to its people, or a private folder. */
export const PLACE_WORDS = { department_only: "Department only", private: "Private" } as const;
export const departmentOnlyHint = (department: string) =>
  `Only ${department}'s people, its head, Management and people given a file directly can see these files.`;
export const privateHint = (department: string | null) =>
  department
    ? `Only the people named on it, the head of ${department} and Management can see this folder.`
    : "Only the people named on it and Management can see this folder.";
export const insidePrivateHint = (folder: string) => `Inside the private folder ${quoted(folder)}.`;

export const SHARED = {
  place: "Shared with me",
  hint: "Files an admin gave to you directly.",
  empty: "Nothing has been shared with you yet.",
  row: "Shared with you", // a file in a folder this person can't see, in place of its path
} as const;

export const NOT_FOUND_TEXT = "It may have been moved or deleted, or you may not have access to it.";
export const NOT_FOUND_DOCUMENT = "We couldn't find this document.";
export const NOT_FOUND_FOLDER = "We couldn't find this folder.";

// --- Who can see this (the Audience panel) -------------------------------------------------------

export const WHO_CAN_SEE = "Who can see this";
export const audienceTitle = (name: string) => `Who can see ${quoted(name)}`;
export const AUDIENCE_LOADING = "Loading who can see this…";
export const AUDIENCE_FORBIDDEN = "Only admins, and the head of this department, can see who has access.";
export const CHECK_PERSON = "Check a person";

/** A group's heading; `place` is the department (head, member) or the private folder (named). */
export function reasonHeading(reason: string, place: string | null): string | null {
  switch (reason) {
    case "management":
      return "Management (sees all files)";
    case "head":
      return place ? `Head of ${place}` : "Head of its department";
    case "member":
      return place ? `In ${place}` : "In its department";
    case "named":
      return place ? `Named on ${quoted(place)}` : "Named on its private folder";
    case "given":
      return "Given this file directly";
    case "held":
      return "Uploaded it, not filed yet";
    case "everyone":
      return "Everyone (this place is open)";
    default:
      return null; // a reason this screen doesn't know yet (locks, PR 12) is left out
  }
}

/** Why one person can see it, after "as": the first reason the server gives; null for one this
 * screen doesn't word (the answer then says only yes). */
function reasonWhy(reason: string | undefined, places: Readonly<Record<string, string>>): string | null {
  switch (reason) {
    case "management":
      return "Management";
    case "head":
      return places.head ? `head of ${places.head}` : "head of its department";
    case "member":
      return places.member ? `a member of ${places.member}` : "a member of its department";
    case "named":
      return places.named ? `someone named on ${quoted(places.named)}` : "someone named on its private folder";
    case "given":
      return "someone given this file directly";
    case "held":
      return "the person who uploaded it";
    default:
      return null;
  }
}

/** Check a person's answer: "Yes. Priya can see this, as a member of Finance." */
export function checkAnswer(name: string, canSee: boolean, reasons: readonly string[], places: Readonly<Record<string, string>>): string {
  if (!canSee) return `No. ${name} can't see this.`;
  if (reasons[0] === "everyone") return `Yes. ${name} can see this, because this place is open to everyone.`;
  const why = reasonWhy(reasons[0], places);
  return why ? `Yes. ${name} can see this, as ${why}.` : `Yes. ${name} can see this.`;
}

/** Past this many people, a group's names fold behind "Show all" (Everyone, in a large organisation). */
export const FOLD_AT = 10;
export const PEOPLE_FAILED = "Couldn't load the people to check.";

export const everyoneElseText = (n: number) =>
  n === 0 ? "Everyone in the organisation can see it." : `Everyone else (${peopleCount(n)}) can't see it.`;

// --- Private, and its named list ------------------------------------------------------------------

export const MAKE_PRIVATE = "Make private";
export const MAKE_PRIVATE_HELP = "Only the people you choose can see the files in this folder."; // #700: the folder's button
export const MAKE_OPEN = "Make open again";
export const NAME_PEOPLE = "Name people who can still see it";
export const NAMED_HEADING = "Named on this folder";
export const NO_ONE_NAMED = "No one is named on this folder.";
export const TAKE_OFF = "Take off folder";
export const makePrivateQuestion = (folder: string) => `Make ${quoted(folder)} private?`;
export const makeOpenQuestion = (folder: string) => `Make ${quoted(folder)} open again?`;
export const privateToast = (folder: string, on: boolean) => (on ? `${folder} is now private.` : `${folder} is open again.`);

/** Make private: who will still see it, and who loses it (the preview counts the people named). */
export function privateText(department: string | null, losing: readonly Person[]): string {
  const who = department ? `Only people you name, the ${department} head and Management will see it.` : "Only people you name and Management will see it.";
  if (losing.length === 0) return `${who} Nobody loses access.`;
  return `${who} ${peopleCount(losing.length)} will lose access: ${namesPreview(losing)}.`;
}
/** The people named on it before (making it private again brings them back), kept unless unticked. */
export const namedBeforeText = (people: readonly Person[]) =>
  `These people were named before and will see it again: ${people.map((person) => person.name).join(", ")}.`;
export const NAMED_BEFORE_LEGEND = "Named before";
export const keepNamed = (name: string) => `Keep ${name} named`;
export const notTakenOff = (names: readonly string[]) => `Couldn't take ${names.join(", ")} off. Try again from the named list.`;

/** Who loses access: the preview's, and the people named before who are unticked (taken off
 * once it is private). */
export function privateLosing(losing: readonly Person[], namedBefore: readonly Person[], dropped: readonly string[]): Person[] {
  const off = namedBefore.filter((person) => dropped.includes(person.id) && !losing.some((each) => each.id === person.id));
  return [...losing, ...off];
}

export function openAgainText(department: string | null, files: number): string {
  const these = files === 0 ? "this folder" : files === 1 ? "this file" : `these ${files} files`;
  return department ? `Everyone who can see ${department} will see ${these}.` : `Everyone in the organisation will see ${these}.`;
}

// --- Give access ---------------------------------------------------------------------------------

export const MAX_GIVE_FILES = 100;
export const MAX_GIVE_PEOPLE = 20;
export const MAX_NAMED = 50;
export const CHOOSE_FILES_CAP = `Choose ${MAX_GIVE_FILES} files or fewer.`;
export const giveTitle = (files: readonly { display_name: string }[]) =>
  files.length === 1 ? `Give access to ${quoted(files[0]?.display_name ?? "")}` : `Give access to ${files.length} files`;
export const giveText = (files: number) =>
  files === 1
    ? "They can open it, and Ask, search and totals can use it for them. Every version is given, now and later."
    : "They can open them, and Ask, search and totals can use these files for them. Every version is given, now and later.";
/** The password step's "access to …" for the files given. */
export const filesWhat = (files: readonly { display_name: string }[]) =>
  files.length === 1 ? quoted(files[0]?.display_name ?? "") : plural(files.length, "file", "files");

// --- kept from me (an Admin's names list) -------------------------------------------------------

/** null for 0: the strip shows nothing then. */
export const keptStripText = (n: number) =>
  n <= 0 ? null : n === 1 ? "1 file here is kept from you." : `${n} files here are kept from you.`;
export const keptHeading = (folder: string) => `Files kept from you in ${quoted(folder)}`;
export const keptShowing = (shown: number, count: number) => `Showing the first ${shown} of ${count}, by name.`;
export const myselfToast = (file: string) => `You can now open ${quoted(file)}.`;
export const KEPT_HINT = "You can see their names because you're an admin. You can't open them.";
export const SHOW_NAMES = "Show names";
export const GIVE_MYSELF = "Give myself access";
// The menus' plain lines (a document's menu, ACTION_HELP, and the names list's).
export const GIVE_HELP = "Let named people open this file, with every version.";
export const AUDIENCE_HELP = "See who can open this file, and why.";
export const MYSELF_HELP = "Asks for your password, tells Management, then lets you open this file.";

// --- moves and folder deletes refused (PR 10c's rules; p1:194-204) -----------------------------

export const MOVE_REFUSED = "You can't move this here, because it would change who can see it. Ask an admin.";
export const FOLDER_DELETE_REFUSED = "You can't delete this folder. Ask an admin.";
/** PR 10c's 403 codes, exactly these (its backend test pins them): said in the server's words, or
 * these when it sends none. */
export const LIBRARY_REFUSALS: Readonly<Record<string, string>> = {
  move_changes_access: MOVE_REFUSED,
  hidden_inside: FOLDER_DELETE_REFUSED,
};

// --- a move's effect, before it is made (PR 10c's previews) -------------------------------------

/** "3 people will lose access: Priya, Amit and Ravi. 1 person will be able to see it." Counts only
 * for the person a file is held for (no names come); "" when the same people see it there. */
export function moveEffectText(preview: MovePreview): string {
  const losing = preview.losing_count ?? preview.losing.length;
  const gaining = preview.gaining_count ?? preview.gaining.length;
  const named = (people: readonly Person[]) => (people.length ? `: ${namesPreview(people)}` : "");
  const parts: string[] = [];
  if (losing > 0) parts.push(`${peopleCount(losing)} will lose access${named(preview.losing)}.`);
  if (gaining > 0) parts.push(`${peopleCount(gaining)} will be able to see it${named(preview.gaining)}.`);
  return parts.join(" ");
}
export const MOVE_ANYWAY = "Move anyway and delete";
/** An admin moved files where they can't see them (PATCH's `{id, hidden: true}`): the move stands,
 * and it can't be undone from here. */
export const hiddenMovedText = (n: number, total: number) =>
  total === 1
    ? "You can't see it there, so it can't be undone here."
    : `You can't see ${n} of them there, so ${n === 1 ? "that one" : "those"} can't be undone here.`;

// --- errors --------------------------------------------------------------------------------------

export const CHOOSE_PEOPLE_CAP = (cap: number) => `Choose ${cap} people or fewer.`;

const ACCESS_ERRORS: Record<string, string> = {
  wrong_password: WRONG_PASSWORD,
  disabled: "This account is disabled. Enable it before giving access.",
  not_a_department: "This isn't a department any more. Reload the page.",
  not_private: "This folder isn't private any more. Reload the page.",
  in_trash: "This file is in Trash. Restore it first.",
  department_in_use: "This department has people or private folders. Remove them first.",
  inside_private: "This folder is inside a private folder, so it's private already.",
  holds_private: "A folder inside this one is private. Make that folder open again first.",
  department: "A department can't be private. Use Department only on the Departments tab instead.",
};

/** Make private refused before asking (the same words as the server's 409s). */
export const privateRefusalText = (code: "department" | "inside_private" | "holds_private") => ACCESS_ERRORS[code] ?? "";

/** An access refusal in its own words; `password_locked` keeps the server's (how long the lock
 * lasts); anything else as `fallback` says it. */
export function accessErrorText(caught: unknown, fallback: (caught: unknown) => string = errorWords): string {
  if (caught instanceof ApiError && Object.hasOwn(ACCESS_ERRORS, caught.code)) return ACCESS_ERRORS[caught.code] ?? "";
  if (caught instanceof ApiError && caught.code === "password_locked") return caught.message;
  return fallback(caught);
}
