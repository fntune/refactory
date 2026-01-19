#!/usr/bin/env node
/**
 * TypeScript refactoring operations using ts-morph.
 * Called by the Python backend via subprocess.
 */
const { Project } = require("ts-morph");
const path = require("path");
const fs = require("fs");

/**
 * Validate that a path stays within project root.
 * @param {string} filePath - The file path to validate
 * @param {string} projectRoot - The project root directory
 * @returns {string} - The resolved absolute path
 * @throws {Error} - If path escapes project root
 */
function validatePath(filePath, projectRoot) {
  const root = path.resolve(projectRoot);
  const resolved = path.resolve(root, filePath);
  if (!resolved.startsWith(root + path.sep) && resolved !== root) {
    throw new Error(`Path '${filePath}' escapes project root`);
  }
  return resolved;
}

/**
 * Get or create a ts-morph Project.
 */
function getProject(projectRoot) {
  const root = path.resolve(projectRoot);
  if (!fs.existsSync(root)) {
    throw new Error(`Project root does not exist: ${projectRoot}`);
  }

  const tsConfigPath = path.join(root, "tsconfig.json");
  if (fs.existsSync(tsConfigPath)) {
    return new Project({ tsConfigFilePath: tsConfigPath });
  }
  return new Project({
    compilerOptions: { allowJs: true, checkJs: false },
  });
}

/**
 * Get the module specifier that an import uses to reference a source file.
 */
function getModuleSpecifierForFile(importDecl, targetSourceFile) {
  const resolvedModule = importDecl.getModuleSpecifierSourceFile();
  if (resolvedModule && resolvedModule.getFilePath() === targetSourceFile.getFilePath()) {
    return importDecl;
  }
  return null;
}

function moveModule(args) {
  const { source, target, projectRoot, dryRun } = args;

  // Validate paths
  validatePath(source, projectRoot);
  validatePath(target, projectRoot);

  const project = getProject(projectRoot);
  const root = path.resolve(projectRoot);

  const sourceFile = project.addSourceFileAtPath(path.join(root, source));
  const targetDir = path.dirname(path.join(root, target));

  // Get all files that import this module BEFORE moving
  const referencingFiles = sourceFile.getReferencingSourceFiles();
  const affectedFiles = [source, ...referencingFiles.map((f) => path.relative(root, f.getFilePath()))];

  if (!dryRun) {
    // Ensure target directory exists
    if (!fs.existsSync(targetDir)) {
      fs.mkdirSync(targetDir, { recursive: true });
    }

    // Move the file
    sourceFile.move(path.join(root, target));

    // Update imports in all referencing files using exact match
    for (const refFile of referencingFiles) {
      const imports = refFile.getImportDeclarations();
      for (const imp of imports) {
        // Check if this import resolves to our moved file
        const resolvedFile = imp.getModuleSpecifierSourceFile();
        if (resolvedFile && resolvedFile.getFilePath() === sourceFile.getFilePath()) {
          // Compute new relative path
          const newPath = path.relative(
            path.dirname(refFile.getFilePath()),
            path.join(root, target)
          );
          let newSpecifier = newPath.startsWith(".") ? newPath : "./" + newPath;
          // Remove extension
          newSpecifier = newSpecifier.replace(/\.(ts|tsx|js|jsx)$/, "");
          imp.setModuleSpecifier(newSpecifier);
        }
      }
    }

    project.saveSync();
  }

  return {
    success: true,
    dry_run: dryRun,
    source,
    target,
    affected_files: affectedFiles,
    changes_count: affectedFiles.length,
  };
}

function moveSymbol(args) {
  const { sourceFile: srcPath, symbolName, targetFile: tgtPath, projectRoot, dryRun } = args;

  // Validate paths
  validatePath(srcPath, projectRoot);
  validatePath(tgtPath, projectRoot);

  const project = getProject(projectRoot);
  const root = path.resolve(projectRoot);

  const sourceFile = project.addSourceFileAtPath(path.join(root, srcPath));
  let targetFile = project.getSourceFile(path.join(root, tgtPath));

  if (!targetFile) {
    if (dryRun) {
      throw new Error(`Target file does not exist: ${tgtPath}`);
    }
    targetFile = project.createSourceFile(path.join(root, tgtPath), "");
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

  // Find all files that reference this symbol
  const referencingNodes = symbol.findReferencesAsNodes ? symbol.findReferencesAsNodes() : [];
  const referencingFiles = [...new Set(referencingNodes.map((n) => n.getSourceFile()))];
  const affectedFiles = [srcPath, tgtPath, ...referencingFiles
    .filter(f => f.getFilePath() !== sourceFile.getFilePath() && f.getFilePath() !== targetFile.getFilePath())
    .map((f) => path.relative(root, f.getFilePath()))];

  if (!dryRun) {
    // Check if symbol is exported
    const isExported = symbol.isExported ? symbol.isExported() : false;

    // Get the full text of the symbol including leading trivia (comments, etc)
    const symbolText = symbol.getFullText();

    // Add to target file with export if it was exported
    if (isExported) {
      targetFile.addStatements(`export ${symbolText.trim()}`);
    } else {
      targetFile.addStatements(symbolText);
    }

    // Update imports in files that reference this symbol
    for (const refFile of referencingFiles) {
      if (refFile.getFilePath() === sourceFile.getFilePath()) continue;
      if (refFile.getFilePath() === targetFile.getFilePath()) continue;

      // Find imports from source file
      const imports = refFile.getImportDeclarations();
      for (const imp of imports) {
        const resolvedFile = imp.getModuleSpecifierSourceFile();
        if (resolvedFile && resolvedFile.getFilePath() === sourceFile.getFilePath()) {
          // Check if this import includes our symbol
          const namedImports = imp.getNamedImports();
          const symbolImport = namedImports.find(n => n.getName() === symbolName);

          if (symbolImport) {
            // Remove this symbol from the import
            symbolImport.remove();

            // If no named imports left, remove the whole import
            if (imp.getNamedImports().length === 0) {
              imp.remove();
            }

            // Add new import from target file
            const newPath = path.relative(
              path.dirname(refFile.getFilePath()),
              targetFile.getFilePath()
            );
            let newSpecifier = newPath.startsWith(".") ? newPath : "./" + newPath;
            newSpecifier = newSpecifier.replace(/\.(ts|tsx|js|jsx)$/, "");

            // Check if import from target already exists
            const existingImport = refFile.getImportDeclaration(newSpecifier);
            if (existingImport) {
              existingImport.addNamedImport(symbolName);
            } else {
              refFile.addImportDeclaration({
                moduleSpecifier: newSpecifier,
                namedImports: [symbolName],
              });
            }
          }
        }
      }
    }

    // Remove from source file
    symbol.remove();

    project.saveSync();
  }

  return {
    success: true,
    dry_run: dryRun,
    symbol: symbolName,
    source: srcPath,
    target: tgtPath,
    affected_files: [...new Set(affectedFiles)],
  };
}

function renameSymbol(args) {
  const { file, oldName, newName, projectRoot, dryRun } = args;

  // Validate path
  validatePath(file, projectRoot);

  const project = getProject(projectRoot);
  const root = path.resolve(projectRoot);

  const sourceFile = project.addSourceFileAtPath(path.join(root, file));

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
  const referencingNodes = symbol.findReferencesAsNodes ? symbol.findReferencesAsNodes() : [];
  const referencingFiles = [...new Set(referencingNodes.map((n) => n.getSourceFile()))];
  const affectedFiles = [file, ...referencingFiles.map((f) => path.relative(root, f.getFilePath()))];

  if (!dryRun) {
    symbol.rename(newName);
    project.saveSync();
  }

  return {
    success: true,
    dry_run: dryRun,
    old_name: oldName,
    new_name: newName,
    file,
    affected_files: [...new Set(affectedFiles)],
  };
}

function validateImports(args) {
  const { projectRoot } = args;
  const root = path.resolve(projectRoot);

  if (!fs.existsSync(root)) {
    return { errors: [{ error: `Project root does not exist: ${projectRoot}`, type: "invalid_root" }] };
  }

  const project = getProject(projectRoot);

  // Add all TS/JS files
  project.addSourceFilesAtPaths([
    path.join(root, "**/*.ts"),
    path.join(root, "**/*.tsx"),
    path.join(root, "**/*.js"),
    path.join(root, "**/*.jsx"),
    "!" + path.join(root, "**/node_modules/**"),
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
        file: path.relative(root, sourceFile.getFilePath()),
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
