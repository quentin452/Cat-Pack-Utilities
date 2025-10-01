# Cat-Pack-Utilities

A comprehensive collection of development utilities designed to help analyze and manage codebases, with special focus on Java/Minecraft mod development.

## Features

### 🔍 **Excessive Calls Finder**
Advanced method call analysis tool that helps identify potentially problematic or excessive method usage in your codebase.

**Key Features:**
- **Smart Pattern Detection**: Automatically detects direct calls, instance calls, static calls, and chained calls
- **Multi-language Support**: Java, JavaScript/TypeScript, Python, C/C++, C#, Go, Rust, PHP, Ruby, Kotlin, Scala, Swift
- **Comprehensive Analysis**: Statistical analysis with detailed reports and issue categorization
- **Configurable Thresholds**: Customize what constitutes "excessive" usage for your project

**Usage Example:**
```bash
# Search for atan2 method calls
Method to search: atan2
Threshold: 10 calls

Results:
📊 Total calls found: 45 across 8 files
🚨 Issues detected: 1 high severity, 2 medium severity
📄 Detailed report saved with line-by-line analysis
```

### 🔎 **Word/Name Searching**
Powerful text search utility for finding specific patterns in codebases.

### 🔄 **Duplicate Mods Finder**
Specialized tool for Minecraft mod development that identifies duplicate mods in your mods folder.

**Features:**
- Detects mods with same modid but different versions
- Identifies suspicious mod files with problematic IDs
- Scans embedded mods within JAR files
- Generates detailed reports with recommendations

### 📊 **Class Counter for JARs**
Analyzes JAR files to count and categorize Java classes.

## Installation

1. Clone the repository:
```bash
git clone https://github.com/quentin452/Cat-Pack-Utilities.git
cd Cat-Pack-Utilities
```

2. Install dependencies:
```bash
python !installdependencies.py
```

3. Run the main application:
```bash
python MainClass.py
```

## Usage

### GUI Interface
Launch the main application to access all utilities through a user-friendly graphical interface:
```bash
python MainClass.py
```

## Output Files

The utilities generate various output files for analysis:
- **Reports**: Detailed analysis results saved in respective `*_reports/` folders
- **Logs**: Execution logs saved in `*_logs/` folders for debugging and audit trails
- **Backups**: Automatic backups of search results and configurations

## Requirements

- Python 3.7+
- tkinter (usually included with Python)
- PIL (Pillow) for image processing
- Additional dependencies installed via `!installdependencies.py`

## Contributing

Contributions are welcome! Please feel free to submit issues, feature requests, or pull requests.

## License

This project is licensed under the terms specified in `LICENCE.txt`.