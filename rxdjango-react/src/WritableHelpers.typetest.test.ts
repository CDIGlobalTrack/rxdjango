import { execSync } from 'child_process';
import * as path from 'path';

describe('Channel-scoped writable type-level test', () => {
  it('Saveable/Deleteable/Creatable compose correctly in the type system', () => {
    const typeTestFile = path.resolve(__dirname, 'WritableHelpers.typetest.ts');

    // Run tsc --noEmit on the type test file.
    // If the types are correct, this should exit 0 (no errors).
    expect(() => {
      execSync(
        `npx tsc --noEmit --strict --esModuleInterop --skipLibCheck --moduleResolution node ${typeTestFile}`,
        { cwd: path.resolve(__dirname, '..'), encoding: 'utf-8' },
      );
    }).not.toThrow();
  });
});
