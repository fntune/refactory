#!/usr/bin/env node
/**
 * TypeScript refactoring operations using ts-morph.
 * Called by the Python backend via subprocess.
 */
const { Project, ts } = require("ts-morph");
const path = require("path");
const fs = require("fs");

function getProject(projectRoot) {
  const tsConfigPath = path.join(projectRoot, "tsconfig.json");
  if (fs.existsSync(tsConfigPath)) {
    return new Project({ tsConfigFilePath: tsConfigPath });
  }
  return new Project({
    compilerOptions: { allowJs: true, checkJs: false },
  });
}

function moveModule(args) {
  const { source, target, projectRoot, dryRun } = args;
  const project = getProject(projectRoot);

  const sourceFile = project.addSourceFileAtPath(path.join(projectRoot, source));
  const targetDir = path.dirname(path.join(projectRoot, target));

  // Ensure target directory exists
  if (!fs.existsSync(targetDir)) {
    fs.mkdirSync(targetDir, { recursive: true });
  }

  // Get all files that import this module
  const referencingFiles = sourceFile.getReferencingSourceFiles();
  const affectedFiles = [source, ...referencingFiles.map((f) => path.relative(projectRoot, f.getFilePath()))];

  if (!dryRun) {
    // Move the file
    sourceFile.move(path.join(projectRoot, target));

    // Update imports in all referencing files
    for (const refFile of referencingFiles) {
      const imports = refFile.getImportDeclarations();
      for (const imp of imports) {
        const moduleSpecifier = imp.getModuleSpecifierValue();
        if (moduleSpecifier.includes(path.basename(source, path.extname(source)))) {
          const newPath = path.relative(
            path.dirname(refFile.getFilePath()),
            path.join(projectRoot, target)
          );
          const newSpecifier = newPath.startsWith(".") ? newPath : "./" + newPath;
          imp.setModuleSpecifier(newSpecifier.replace(/\.(ts|tsx|js|jsx)$/, ""));
        }
      }
    }

    project.saveSync();
  }

  return {
    success: true,
    dryRun,
    source,
    target,
    affectedFiles,
    changesCount: affectedFiles.length,
  };
}

function moveSymbol(args) {
  const { sourceFile: srcPath, symbolName, targetFile: tgtPath, projectRoot, dryRun } = args;
  const project = getProject(projectRoot);

  const sourceFile = project.addSourceFileAtPath(path.join(projectRoot, srcPath));
  let targetFile = project.getSourceFile(path.join(projectRoot, tgtPath));

  if (!targetFile) {
    targetFile = project.createSourceFile(path.join(projectRoot, tgtPath), "");
  }

  // Find the symbol
  const symbol =
    sourceFile.getFunction(symbolName) ||
    sourceFile.getClass(symbolName) ||
    sourceFile.getInterface(symbolName) ||
    sourceFile.getTypeAlias(symbolName) ||
    sourceFile.getVariableDeclaration(symbolName);

  if (!symbol) {
    throw new Error(`Symbol '${symbolName}' not found in ${srcPath}`);
  }

  const affectedFiles = [srcPath, tgtPath];

  if (!dryRun) {
    // Get the full text of the symbol
    const symbolText = symbol.getFullText();

    // Add to target file
    targetFile.addStatements(symbolText);

    // Remove from source
    symbol.remove();

    // Update imports
    const referencingFiles = sourceFile.getReferencingSourceFiles();
    for (const refFile of referencingFiles) {
      affectedFiles.push(path.relative(projectRoot, refFile.getFilePath()));
    }

    project.saveSync();
  }

  return {
    success: true,
    dryRun,
    symbol: symbolName,
    source: srcPath,
    target: tgtPath,
    affectedFiles,
  };
}

function renameSymbol(args) {
  const { file, oldName, newName, projectRoot, dryRun } = args;
  const project = getProject(projectRoot);

  const sourceFile = project.addSourceFileAtPath(path.join(projectRoot, file));

  // Find the symbol
  const symbol =
    sourceFile.getFunction(oldName) ||
    sourceFile.getClass(oldName) ||
    sourceFile.getInterface(oldName) ||
    sourceFile.getTypeAlias(oldName) ||
    sourceFile.getVariableDeclaration(oldName);

  if (!symbol) {
    throw new Error(`Symbol '${oldName}' not found in ${file}`);
  }

  // Get all files that reference this symbol
  const referencingFiles = symbol.findReferencesAsNodes
    ? symbol.findReferencesAsNodes().map((n) => n.getSourceFile())
    : [];
  const affectedFiles = [file, ...new Set(referencingFiles.map((f) => path.relative(projectRoot, f.getFilePath())))];

  if (!dryRun) {
    symbol.rename(newName);
    project.saveSync();
  }

  return {
    success: true,
    dryRun,
    oldName,
    newName,
    file,
    affectedFiles: [...new Set(affectedFiles)],
  };
}

function validateImports(args) {
  const { projectRoot } = args;
  const project = getProject(projectRoot);

  // Add all TS/JS files
  project.addSourceFilesAtPaths([
    path.join(projectRoot, "**/*.ts"),
    path.join(projectRoot, "**/*.tsx"),
    path.join(projectRoot, "**/*.js"),
    path.join(projectRoot, "**/*.jsx"),
    "!" + path.join(projectRoot, "**/node_modules/**"),
  ]);

  const errors = [];
  const diagnostics = project.getPreEmitDiagnostics();

  for (const diag of diagnostics) {
    const sourceFile = diag.getSourceFile();
    if (!sourceFile) continue;

    const message = diag.getMessageText();
    const messageText = typeof message === "string" ? message : message.getMessageText();

    if (messageText.includes("Cannot find module") || messageText.includes("has no exported member")) {
      errors.push({
        file: path.relative(projectRoot, sourceFile.getFilePath()),
        line: diag.getLineNumber() || 0,
        error: messageText,
        type: "import_error",
      });
    }
  }

  return { errors };
}

// Main
const [operation, argsJson] = process.argv.slice(2);
const args = JSON.parse(argsJson);

const operations = {
  move_module: moveModule,
  move_symbol: moveSymbol,
  rename_symbol: renameSymbol,
  validate_imports: validateImports,
};

if (!operations[operation]) {
  console.error(JSON.stringify({ error: `Unknown operation: ${operation}` }));
  process.exit(1);
}

try {
  const result = operations[operation](args);
  console.log(JSON.stringify(result));
} catch (error) {
  console.error(JSON.stringify({ error: error.message }));
  process.exit(1);
}
