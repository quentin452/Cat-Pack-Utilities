package main.fr.iamacat.catpackutilities;

import com.badlogic.gdx.ApplicationAdapter;
import com.badlogic.gdx.Gdx;
import com.badlogic.gdx.Screen;
import com.badlogic.gdx.graphics.GL20;
import com.badlogic.gdx.graphics.Texture;
import com.badlogic.gdx.graphics.g2d.SpriteBatch;
import com.badlogic.gdx.scenes.scene2d.Actor;
import com.badlogic.gdx.scenes.scene2d.Stage;
import com.badlogic.gdx.scenes.scene2d.ui.ImageButton;
import com.badlogic.gdx.scenes.scene2d.utils.ChangeListener;
import com.badlogic.gdx.scenes.scene2d.utils.TextureRegionDrawable;
import main.fr.iamacat.catpackutilities.utilities.WordOrNameSearchingScreen;

public class CatPackUtilities extends ApplicationAdapter {
    private Stage stage;
    private SpriteBatch batch;
    private Texture backgroundImage;
    private Texture buttonTexture;
    private float backgroundWidth;
    private float backgroundHeight;
    private WordOrNameSearchingScreen searchingScreen;
    private boolean isSearchingScreenVisible;

    @Override
    public void create() {
        batch = new SpriteBatch();
        stage = new Stage();

        stage.clear();

        searchingScreen = new WordOrNameSearchingScreen(this);

        backgroundImage = new Texture(Gdx.files.internal("test.png"));
        buttonTexture = new Texture(Gdx.files.internal("button1.png"));

        ImageButton button = getImageButton();

        stage.addActor(button);
        Gdx.input.setInputProcessor(stage);

        backgroundWidth = Gdx.graphics.getWidth();
        backgroundHeight = Gdx.graphics.getHeight();
    }

    private ImageButton getImageButton() {
        ImageButton button = new ImageButton(new TextureRegionDrawable(buttonTexture));
        button.setPosition(Gdx.graphics.getWidth() / 2f - button.getWidth() / 2f,
                Gdx.graphics.getHeight() / 2f - button.getHeight() / 2f);

        button.addListener(new ChangeListener() {
            @Override
            public void changed(ChangeEvent event, Actor actor) {
                Gdx.app.log("CatPackUtilities", "Button clicked");
                showSearchingScreen();
            }
        });
        return button;
    }

    private void showSearchingScreen() {
        isSearchingScreenVisible = true;
        searchingScreen.show();
        Gdx.input.setInputProcessor(searchingScreen.getStage());
    }

    @Override
    public void render() {
        Gdx.gl.glClearColor(1, 1, 1, 1);
        Gdx.gl.glClear(GL20.GL_COLOR_BUFFER_BIT);

        batch.begin();
        batch.draw(backgroundImage, 0, 0, backgroundWidth, backgroundHeight);
        batch.end();

        stage.act();
        stage.draw();

        if (isSearchingScreenVisible) {
            searchingScreen.render(Gdx.graphics.getDeltaTime());
        }
    }

    public void showMainMenu() {
        isSearchingScreenVisible = false;
        searchingScreen.hide();
        Gdx.input.setInputProcessor(stage);
    }

    @Override
    public void dispose() {
        batch.dispose();
        backgroundImage.dispose();
        buttonTexture.dispose();
        stage.dispose();
        searchingScreen.dispose();
    }
    @Override
    public void pause() {
        Gdx.input.setInputProcessor(null);
    }
    private Screen currentScreen;
    public void setScreen(Screen screen) {
        if(currentScreen != null) {
            currentScreen.hide();
        }

        currentScreen = screen;

        // Ne pas afficher immédiatement
        //currentScreen.show();
    }
}