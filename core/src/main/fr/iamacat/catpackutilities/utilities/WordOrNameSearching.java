package main.fr.iamacat.catpackutilities.utilities;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileReader;
import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import java.util.Scanner;

public class WordOrNameSearching {
    public static void main(String[] args) {
        configureLogging();
        searchFiles();
    }

    private static void configureLogging() {
        // Configure logging - not covered in the Java version
        // You may use java.util.logging or other logging libraries as per your preference
        // Basic logging configuration could include setting up log files, log levels, etc.
    }

    public static void searchFiles() {
        Scanner scanner = new Scanner(System.in);

        while (true) {
            System.out.print("Enter the root folder path to search (or 'exit' to quit): ");
            String rootDir = scanner.nextLine();

            if (rootDir.equalsIgnoreCase("exit")) {
                System.exit(0);
            }

            File directory = new File(rootDir);

            if (!directory.isDirectory()) {
                System.out.println("Invalid directory path or directory does not exist.");
                // Log error message - not covered in the Java version
                continue;
            }

            System.out.print("Enter the text you want to find (or 'exit' to change root folder): ");
            String query = scanner.nextLine();

            if (query.equalsIgnoreCase("exit")) {
                break;
            }

            if (query.trim().isEmpty()) {
                System.out.println("Please enter a valid search text or type 'exit' to change root folder.");
                continue;
            }

            System.out.println("\nChoose printing behavior:");
            System.out.println("1. Print the whole line");
            System.out.println("2. Print only lines containing the query");

            System.out.print("Enter your choice (1 or 2): ");
            String printOption = scanner.nextLine();

            if (!printOption.equals("1") && !printOption.equals("2")) {
                System.out.println("Invalid choice. Please enter '1' or '2'.");
                continue;
            }

            boolean names = false; // "--names" in sys.argv
            boolean deepScan = !names;

            String configFilePath = "config.txt";

            List<String> excludedExtensions = new ArrayList<>();
            try (BufferedReader reader = new BufferedReader(new FileReader(configFilePath))) {
                String line;
                while ((line = reader.readLine()) != null) {
                    line = line.trim();
                    if (!line.isEmpty() && !line.startsWith("#")) {
                        String[] parts = line.split("=");
                        if (parts.length == 2 && parts[1].equalsIgnoreCase("true")) {
                            excludedExtensions.add(parts[0]);
                        }
                    }
                }
            } catch (IOException e) {
                e.printStackTrace();
            }

            searchFilesInDirectory(directory, query, printOption, deepScan, excludedExtensions);
        }
    }

    private static void searchFilesInDirectory(File directory, String query, String printOption,
                                               boolean deepScan, List<String> excludedExtensions) {
        for (File file : directory.listFiles()) {
            if (file.isDirectory()) {
                if (deepScan) {
                    searchFilesInDirectory(file, query, printOption, deepScan, excludedExtensions);
                }
            } else {
                String fileName = file.getName().toLowerCase();
                if (!excludedExtensions.stream().anyMatch(fileName::endsWith)) {
                    List<String> foundLines = searchFileContents(file, query, deepScan);
                    if (!foundLines.isEmpty()) {
                        logResults(file, printOption, foundLines);
                    }
                }
            }
        }
    }

    private static List<String> searchFileContents(File file, String query, boolean deepScan) {
        List<String> foundLines = new ArrayList<>();
        try (BufferedReader reader = new BufferedReader(new FileReader(file))) {
            String line;
            while ((line = reader.readLine()) != null) {
                if ((deepScan && line.toLowerCase().contains(query.toLowerCase())) ||
                        (!deepScan && file.getName().toLowerCase().contains(query.toLowerCase()))) {
                    foundLines.add(line);
                }
            }
        } catch (IOException e) {
            e.printStackTrace();
        }
        return foundLines;
    }

    private static void logResults(File file, String printOption, List<String> foundLines) {
        if (printOption.equals("1")) {
            for (String line : foundLines) {
                System.out.println(line);
            }
        } else if (printOption.equals("2")) {
            System.out.println(file.getName());
        }
    }
}
