// PRD 7 PR 11a (#604): the access screens' words, as section 4 of the plan writes them.

import { describe, expect, it } from "vitest";
import {
  DISABLED_BANNER,
  accessErrorText,
  addedPeopleText,
  addedPeopleToast,
  byLine,
  departmentOnlyText,
  departmentOnlyToast,
  departmentsText,
  headLine,
  headToast,
  makeManagementQuestion,
  managementToast,
  namesPreview,
  newDepartmentToast,
  openText,
  peopleCount,
  removeDepartmentQuestion,
  removeFileQuestion,
  removeFolderQuestion,
  removeHeadQuestion,
  selfGrantText,
  sharedToast,
  stopManagementQuestion,
} from "@/components/access/words";
import { ApiError } from "@/lib/api";
import { NOT_FOUND } from "@/lib/errorWords";

const people = (...names: string[]) => names.map((name, i) => ({ id: `u${i}`, name }));
const many = (n: number) => people("Priya", "Amit", ...Array.from({ length: n - 2 }, (_, i) => `P${i}`));

describe("names and counts", () => {
  it("previews names, then 'and N more'", () => {
    expect(namesPreview(people("Priya"))).toBe("Priya");
    expect(namesPreview(people("Priya", "Amit"))).toBe("Priya and Amit");
    expect(namesPreview(people("Priya", "Amit", "Ravi"))).toBe("Priya, Amit and Ravi");
    expect(namesPreview(many(14))).toBe("Priya, Amit and 12 more");
  });

  it("counts people", () => {
    expect(peopleCount(1)).toBe("1 person");
    expect(peopleCount(12)).toBe("12 people");
  });

  it("lists a person's departments, heads marked", () => {
    expect(departmentsText([{ name: "Finance", head: true }, { name: "Sales", head: false }])).toBe("Finance (head), Sales");
    expect(departmentsText([])).toBe("No department");
    expect(headLine(people("Priya Shah"))).toBe("Head: Priya Shah");
    expect(headLine([])).toBe("No head yet");
  });

  it("says who did it, or only when", () => {
    expect(byLine("Added", "3 Nov 2026", "Ravi")).toBe("Added 3 Nov 2026 by Ravi");
    expect(byLine("Given", "3 Nov 2026", null)).toBe("Given 3 Nov 2026");
  });
});

describe("toasts", () => {
  it("say what changed", () => {
    expect(sharedToast(1, people("Priya"))).toBe("Shared with Priya.");
    expect(sharedToast(1, people("Priya", "Amit"))).toBe("Shared with Priya and Amit.");
    expect(sharedToast(1, people("Priya", "Amit", "Ravi"))).toBe("Shared with 3 people.");
    expect(sharedToast(8, people("Priya"))).toBe("8 files shared with Priya.");
    expect(departmentOnlyToast("Finance", true)).toBe("Finance is now for its people only.");
    expect(departmentOnlyToast("Finance", false)).toBe("Finance is open to everyone again.");
    expect(headToast("Priya", "Finance", true)).toBe("Priya is now head of Finance.");
    expect(headToast("Priya", "Finance", false)).toBe("Priya is no longer head of Finance.");
    expect(managementToast("Priya", true)).toBe("Priya: now Management.");
    expect(managementToast("Priya", false)).toBe("Priya: no longer Management.");
    expect(newDepartmentToast("Finance", "Priya")).toBe("Finance added. Priya heads it.");
    expect(newDepartmentToast("Finance", null)).toBe("Finance added. No head yet.");
    expect(addedPeopleToast(3, "Finance")).toBe("Added 3 people to Finance.");
    expect(addedPeopleText(2, "Finance", [{ person: { id: "r", name: "Ravi" }, reason: "This account is disabled. Enable it before giving access." }])).toBe(
      "Added 2 people to Finance. Couldn't add Ravi: This account is disabled. Enable it before giving access.",
    );
    expect(addedPeopleText(0, "Finance", [])).toBeNull(); // they were all in it already
    expect(DISABLED_BANNER).toBe("This account is disabled. Enable it from the Users tab to give access again.");
  });
});

describe("questions", () => {
  it("asks before Department only, with who loses access", () => {
    expect(departmentOnlyText("Finance", many(14), 3)).toBe(
      "14 people will no longer see Finance files: Priya, Amit and 12 more. Management and anyone you add to Finance still will.",
    );
    expect(departmentOnlyText("Finance", [], 3)).toBe(
      "Nobody loses access: everyone who sees Finance files today is in Finance or is Management.",
    );
    expect(departmentOnlyText("Finance", people("Priya"), 0)).toBe(
      "1 person will no longer see Finance files: Priya. Management and anyone you add to Finance still will. No one is in Finance yet, so only Management will see its files.",
    );
    // Changed on purpose (#604 review): nobody loses and nobody is in it says only what is true.
    expect(departmentOnlyText("Finance", [], 0)).toBe(
      "Nobody loses access. No one is in Finance yet, so only Management will see its files.",
    );
    expect(openText("Finance", 120)).toBe("Everyone in the organisation will be able to see Finance's 120 files.");
    expect(openText("Finance", 1)).toBe("Everyone in the organisation will be able to see Finance's 1 file.");
  });

  it("asks before Management and each remove", () => {
    expect(makeManagementQuestion("Priya")).toBe(
      "Make Priya Management? Priya will see every file in the organisation, including private folders. Management and the other admins get an email about it.",
    );
    expect(stopManagementQuestion("Priya")).toBe(
      "Stop Management for Priya? Priya will see only their departments' files, private folders they are named on and files given to them.",
    );
    expect(removeDepartmentQuestion("Priya", "Finance")).toBe("Remove Priya from Finance? Files given to Priya directly stay given.");
    expect(removeFolderQuestion("Priya", "Salaries")).toBe(
      "Take Priya off “Salaries”? Priya won't see this folder any more, unless they are Management or head of its department.",
    );
    expect(removeHeadQuestion("Priya", "Finance")).toBe(
      "Remove Priya as head of Finance? Priya won't see Finance's private folders any more, unless they are named on them.",
    );
    expect(removeFileQuestion("Priya", "Sale deed.pdf")).toBe(
      "Remove Priya's access to “Sale deed.pdf”? This takes away every version of the file.",
    );
  });

  it("names what the admin gives themselves, never a file in the email", () => {
    expect(selfGrantText("Finance")).toBe(
      "You're giving yourself access to Finance. Enter your Kaagaz password to go on. Management and the other admins will get an email saying you gave yourself access. The email doesn't name any file.",
    );
  });
});

describe("accessErrorText", () => {
  const refused = (status: number, code: string, message = "The server's words.") => new ApiError(status, code, message);

  it("says each access refusal in its own words", () => {
    expect(accessErrorText(refused(400, "wrong_password"))).toBe("That password isn't right. Try again.");
    expect(accessErrorText(refused(400, "password_locked", "Try again in 15 minutes."))).toBe("Try again in 15 minutes.");
    expect(accessErrorText(refused(409, "disabled"))).toBe("This account is disabled. Enable it before giving access.");
    expect(accessErrorText(refused(409, "not_a_department"))).toBe("This isn't a department any more. Reload the page.");
    expect(accessErrorText(refused(409, "not_private"))).toBe("This folder isn't private any more. Reload the page.");
    expect(accessErrorText(refused(409, "in_trash"))).toBe("This file is in Trash. Restore it first.");
    expect(accessErrorText(refused(409, "department_in_use"))).toBe(
      "This department has people or private folders. Remove them first.",
    );
  });

  it("leaves everything else to errorWords, or the caller's own words", () => {
    expect(accessErrorText(refused(404, "not_found"))).toBe(NOT_FOUND);
    expect(accessErrorText(refused(409, "toString"))).toBe("The server's words.");
    expect(accessErrorText(refused(409, "conflict"), () => "Mine.")).toBe("Mine.");
  });
});
