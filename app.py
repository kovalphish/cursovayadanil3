# ТехноМаркет — интернет-магазин бытовой техники
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_from_directory, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import os, uuid
from functools import wraps
from werkzeug.utils import secure_filename
import logging

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev_key_2026')
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# БД
db_url = os.environ.get('DATABASE_URL', 'sqlite:///shop.db')
app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db = SQLAlchemy(app)
ADMIN_PASS = os.environ.get('ADMIN_PASSWORD', 'admin123')

# --- Модели ---
class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(100))
    price = db.Column(db.Float, nullable=False)
    stock = db.Column(db.Integer, default=0)
    description = db.Column(db.Text)
    image = db.Column(db.String(200), default='default.png')

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_name = db.Column(db.String(200), nullable=False)
    customer_phone = db.Column(db.String(50))
    customer_address = db.Column(db.Text)
    total = db.Column(db.Float, default=0)
    status = db.Column(db.String(50), default='new')
    date = db.Column(db.DateTime, default=datetime.utcnow)
    items = db.relationship('OrderItem', backref='order', cascade='all, delete-orphan')

class OrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'))
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'))
    product_name = db.Column(db.String(200))
    price = db.Column(db.Float)
    quantity = db.Column(db.Integer, default=1)

# --- Утилиты ---
@app.teardown_appcontext
def close_db(exc):
    db.session.remove()

def login_required(f):
    @wraps(f)
    def wrap(*a, **kw):
        if not session.get('admin'):
            return redirect(url_for('admin_login'))
        return f(*a, **kw)
    return wrap

def allowed_file(fn):
    return '.' in fn and fn.rsplit('.',1)[1].lower() in {'png','jpg','jpeg','gif','webp'}

def save_image(img_file):
    """Сохраняет изображение с уникальным именем"""
    if not img_file or not img_file.filename:
        return 'default.png'
    
    fn = secure_filename(img_file.filename)
    ext = fn.rsplit('.',1)[1].lower()
    # Генерируем уникальное имя для каждого изображения
    unique_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.{ext}"
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_name)
    img_file.save(filepath)
    
    # Проверяем что файл сохранился
    if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
        return unique_name
    return 'default.png'

# --- API: товары (для корзины на JS) ---
@app.route('/api/products')
def api_products():
    products = Product.query.all()
    return jsonify([{'id':p.id,'name':p.name,'price':p.price,'image':p.image,'stock':p.stock,'category':p.category or ''} for p in products])

# --- Страницы ---
@app.route('/')
def index():
    products = Product.query.limit(12).all()
    return render_template('index.html', products=products)

@app.route('/products')
def products_page():
    search = request.args.get('search','')
    cat = request.args.get('category','')
    q = Product.query
    if search:
        q = q.filter(Product.name.ilike(f'%{search}%'))
    if cat:
        q = q.filter_by(category=cat)
    
    prods = q.all()
    categories = [c[0] for c in db.session.query(Product.category).distinct() if c[0]]
    return render_template('products.html', products=prods, categories=categories, search=search, current_category=cat)

@app.route('/product/<int:id>')
def product_detail(id):
    p = Product.query.get_or_404(id)
    return render_template('product_detail.html', product=p)

# --- Корзина ---
@app.route('/cart')
def cart_page():
    return render_template('cart.html')

@app.route('/checkout', methods=['POST'])
def checkout():
    data = request.get_json(force=True)
    if not data or not data.get('items'):
        return jsonify({'error':'Корзина пуста'}), 400
    
    name = data.get('name','').strip()
    phone = data.get('phone','').strip()
    address = data.get('address','').strip()
    
    if not all([name, phone, address]):
        return jsonify({'error':'Заполните все поля'}), 400
    
    try:
        total = sum(i['price']*i['qty'] for i in data['items'])
        order = Order(customer_name=name, customer_phone=phone, customer_address=address, total=total)
        db.session.add(order)
        db.session.flush()
        
        for i in data['items']:
            db.session.add(OrderItem(order_id=order.id, product_id=i['id'], product_name=i['name'], price=i['price'], quantity=i['qty']))
            p = Product.query.get(i['id'])
            if p:
                p.stock = max(0, p.stock - i['qty'])
        
        db.session.commit()
        return jsonify({'ok':True, 'order_id':order.id})
    except Exception as e:
        db.session.rollback()
        logger.error(f'Checkout: {e}')
        return jsonify({'error':'Ошибка оформления'}), 500

# --- Админка ---
@app.route('/admin/login', methods=['GET','POST'])
def admin_login():
    if request.method == 'POST':
        if request.form.get('password') == ADMIN_PASS:
            session['admin'] = True
            return redirect(url_for('admin'))
        flash('Неверный пароль','error')
    return render_template('admin_login.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin', None)
    return redirect(url_for('index'))

@app.route('/admin')
@login_required
def admin():
    prods = Product.query.all()
    orders = Order.query.order_by(Order.date.desc()).all()
    return render_template('admin.html', products=prods, orders=orders)

@app.route('/admin/product/add', methods=['GET','POST'])
@login_required
def product_add():
    if request.method == 'POST':
        try:
            img_name = 'default.png'
            f = request.files.get('image')
            if f and f.filename and allowed_file(f.filename):
                img_name = save_image(f)
            
            p = Product(
                name=request.form['name'],
                category=request.form.get('category',''),
                price=float(request.form['price']),
                stock=int(request.form.get('stock',0) or 0),
                description=request.form.get('description',''),
                image=img_name
            )
            db.session.add(p)
            db.session.commit()
            flash('Товар успешно добавлен', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Ошибка: {e}', 'error')
        return redirect(url_for('admin'))
    return render_template('product_form.html', title='Добавить товар', product=None)

@app.route('/admin/product/edit/<int:id>', methods=['GET','POST'])
@login_required
def product_edit(id):
    p = Product.query.get_or_404(id)
    if request.method == 'POST':
        try:
            # Сохраняем старое изображение для возможного удаления
            old_image = p.image
            
            # Обновляем данные
            p.name = request.form['name']
            p.category = request.form.get('category','')
            p.price = float(request.form['price'])
            p.stock = int(request.form.get('stock',0) or 0)
            p.description = request.form.get('description','')
            
            # Обрабатываем новое изображение
            f = request.files.get('image')
            if f and f.filename and allowed_file(f.filename):
                # Сохраняем новое изображение
                new_image = save_image(f)
                if new_image and new_image != 'default.png':
                    p.image = new_image
                    # Удаляем старое изображение, если оно не default
                    if old_image and old_image != 'default.png':
                        old_path = os.path.join(app.config['UPLOAD_FOLDER'], old_image)
                        if os.path.exists(old_path):
                            os.remove(old_path)
            
            db.session.commit()
            flash('Товар успешно обновлен', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Ошибка: {e}', 'error')
        return redirect(url_for('admin'))
    return render_template('product_form.html', title='Редактировать товар', product=p)

@app.route('/admin/product/delete/<int:id>')
@login_required
def product_delete(id):
    try:
        p = Product.query.get_or_404(id)
        # Удаляем изображение если оно не default
        if p.image and p.image != 'default.png':
            img_path = os.path.join(app.config['UPLOAD_FOLDER'], p.image)
            if os.path.exists(img_path):
                os.remove(img_path)
        
        db.session.delete(p)
        db.session.commit()
        flash('Товар удален', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Ошибка: {e}', 'error')
    return redirect(url_for('admin'))

@app.route('/admin/order/status/<int:id>', methods=['POST'])
@login_required
def order_status(id):
    try:
        o = Order.query.get_or_404(id)
        o.status = request.form.get('status','new')
        db.session.commit()
        flash('Статус обновлен', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Ошибка: {e}', 'error')
    return redirect(url_for('admin'))

@app.route('/admin/order/delete/<int:id>')
@login_required
def order_delete(id):
    try:
        db.session.delete(Order.query.get_or_404(id))
        db.session.commit()
        flash('Заказ удален', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Ошибка: {e}', 'error')
    return redirect(url_for('admin'))

# --- Статика ---
@app.route('/static/uploads/<fn>')
def uploaded_file(fn):
    return send_from_directory(app.config['UPLOAD_FOLDER'], fn)

@app.errorhandler(404)
def e404(e):
    return render_template('index.html', products=[]), 404

# --- Инициализация ---
with app.app_context():
    db.create_all()

application = app

if __name__ == '__main__':
    app.run(debug=True)
