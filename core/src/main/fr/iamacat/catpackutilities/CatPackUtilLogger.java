package main.fr.iamacat.catpackutilities;

import java.io.File;
import java.io.IOException;
import java.util.logging.*;

public class CatPackUtilLogger {
    private static final Logger LOGGER = Logger.getLogger(CatPackUtilLogger.class.getName());

    public static void main(String[] args) {
        configureLogging();
        LOGGER.info("Hello, this is an example log message!");
    }

    private static void configureLogging() {
        String logsDir = System.getProperty("user.dir") + "/logs";
        File dir = new File(logsDir);
        if (!dir.exists()) {
            dir.mkdir();
        }

        String logFilePath = logsDir + "/app_logs.log";
        Level logLevel = Level.INFO;

        try {
            FileHandler fileHandler = new FileHandler(logFilePath, true);
            fileHandler.setFormatter(new SimpleFormatter());
            fileHandler.setLevel(logLevel);

            ConsoleHandler consoleHandler = new ConsoleHandler();
            consoleHandler.setFormatter(new SimpleFormatter());
            consoleHandler.setLevel(Level.INFO);

            Logger rootLogger = Logger.getLogger("");
            rootLogger.addHandler(fileHandler);
            rootLogger.addHandler(consoleHandler);
            rootLogger.setLevel(logLevel);
        } catch (IOException e) {
            LOGGER.log(Level.SEVERE, "Erreur lors de la configuration du fichier de log : " + logFilePath, e);
        }
    }
}